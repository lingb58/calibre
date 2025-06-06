#!/usr/bin/env python


__license__ = 'GPL v3'
__copyright__ = '2013, Kovid Goyal <kovid at kovidgoyal.net>'

import sys

from qt.core import (
    QAbstractItemView,
    QApplication,
    QIcon,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPalette,
    QSplitter,
    QStyledItemDelegate,
    Qt,
    QTextBrowser,
    pyqtSignal,
)

from calibre.ebooks.oeb.polish.check.base import CRITICAL, DEBUG, ERROR, INFO, WARN
from calibre.ebooks.oeb.polish.check.main import fix_errors, run_checks
from calibre.gui2 import NO_URL_FORMATTING, safe_open_url
from calibre.gui2.tweak_book import tprefs
from calibre.gui2.widgets import BusyCursor


def icon_for_level(level):
    if level > WARN:
        icon = 'dialog_error.png'
    elif level == WARN:
        icon = 'dialog_warning.png'
    elif level == INFO:
        icon = 'dialog_information.png'
    else:
        icon = None
    return QIcon.ic(icon) if icon else QIcon()


def prefix_for_level(level):
    if level > WARN:
        text = _('ERROR')
    elif level == WARN:
        text = _('WARNING')
    elif level == INFO:
        text = _('INFO')
    else:
        text = ''
    if text:
        text += ': '
    return text


def build_error_message(error, with_level=False, with_line_numbers=False):
    prefix = ''
    filename = error.name
    if with_level:
        prefix = prefix_for_level(error.level)
    if with_line_numbers and error.line:
        filename = f'{filename}:{error.line}'
    return f'{prefix}{error.msg}\xa0\xa0\xa0\xa0[{filename}]'


class Delegate(QStyledItemDelegate):

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.row() == self.parent().currentRow():
            option.font.setBold(True)
            option.backgroundBrush = self.parent().palette().brush(QPalette.ColorRole.AlternateBase)


class Check(QSplitter):

    item_activated = pyqtSignal(object)
    check_requested = pyqtSignal()
    fix_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        QSplitter.__init__(self, parent)
        self.setChildrenCollapsible(False)

        self.items = i = QListWidget(self)
        i.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        i.customContextMenuRequested.connect(self.context_menu)
        self.items.setSpacing(3)
        self.items.itemDoubleClicked.connect(self.current_item_activated)
        self.items.currentItemChanged.connect(self.current_item_changed)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.delegate = Delegate(self.items)
        self.items.setItemDelegate(self.delegate)
        self.addWidget(i)
        self.help = h = QTextBrowser(self)
        h.anchorClicked.connect(self.link_clicked)
        h.setOpenLinks(False)
        self.addWidget(h)
        self.setStretchFactor(0, 100)
        self.setStretchFactor(1, 50)
        self.clear_at_startup()

        state = tprefs.get('check-book-splitter-state', None)
        if state is not None:
            self.restoreState(state)

    def clear_at_startup(self):
        self.clear_help(_('Check has not been run'))
        self.items.clear()

    def context_menu(self, pos):
        m = QMenu(self)
        if self.items.count() > 0:
            m.addAction(QIcon.ic('edit-copy.png'), _('Copy list of errors to clipboard'), self.copy_to_clipboard)
        if list(m.actions()):
            m.exec(self.mapToGlobal(pos))

    def copy_to_clipboard(self):
        items = []
        for item in (self.items.item(i) for i in range(self.items.count())):
            err = item.data(Qt.ItemDataRole.UserRole)
            msg = build_error_message(err, with_level=True, with_line_numbers=True)
            items.append(msg)
        if items:
            QApplication.clipboard().setText('\n'.join(items))

    def save_state(self):
        tprefs.set('check-book-splitter-state', bytearray(self.saveState()))

    def clear_help(self, msg=None):
        if msg is None:
            msg = _('No problems found')
        self.help.setText('<h2>{}</h2><p><a style="text-decoration:none" title="{}" href="run:check">{}</a></p>'.format(
            msg, _('Click to run a check on the book'), _('Run check')))

    def link_clicked(self, url):
        url = str(url.toString(NO_URL_FORMATTING))
        if url == 'activate:item':
            self.current_item_activated()
        elif url == 'run:check':
            self.check_requested.emit()
        elif url == 'fix:errors':
            errors = [self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())]
            self.fix_requested.emit(errors)
        elif url.startswith('fix:error,'):
            num = int(url.rpartition(',')[-1])
            errors = [self.items.item(num).data(Qt.ItemDataRole.UserRole)]
            self.fix_requested.emit(errors)
        elif url.startswith('activate:item:'):
            index = int(url.rpartition(':')[-1])
            self.location_activated(index)
        elif url.startswith('https://'):
            safe_open_url(url)

    def next_error(self, delta=1):
        row = self.items.currentRow()
        num = self.items.count()
        if num > 0:
            row = (row + delta) % num
            self.items.setCurrentRow(row)
            self.current_item_activated()

    def current_item_activated(self, *args):
        i = self.items.currentItem()
        if i is not None:
            err = i.data(Qt.ItemDataRole.UserRole)
            if err.has_multiple_locations:
                self.location_activated(0)
            else:
                self.item_activated.emit(err)

    def location_activated(self, index):
        i = self.items.currentItem()
        if i is not None:
            err = i.data(Qt.ItemDataRole.UserRole)
            err.current_location_index = index
            self.item_activated.emit(err)

    def current_item_changed(self, *args):
        i = self.items.currentItem()
        self.help.setText('')

        def loc_to_string(line, col):
            loc = ''
            if line is not None:
                loc = _('line: %d') % line
            if col is not None:
                loc += _(' column: %d') % col
            if loc:
                loc = f' ({loc})'
            return loc

        if i is not None:
            err = i.data(Qt.ItemDataRole.UserRole)
            header = {DEBUG:_('Debug'), INFO:_('Information'), WARN:_('Warning'), ERROR:_('Error'), CRITICAL:_('Error')}[err.level]
            ifix = ''
            loc = loc_to_string(err.line, err.col)
            if err.INDIVIDUAL_FIX:
                ifix = f"<a href=\"fix:error,{self.items.currentRow()}\" title=\"{_('Try to fix only this error')}\">{err.INDIVIDUAL_FIX}</a><br><br>"
            open_tt = _('Click to open in editor')
            fix_tt = _('Try to fix all fixable errors automatically. Only works for some types of error.')
            fix_msg = _('Try to correct all fixable errors automatically')
            run_tt, run_msg = _('Re-run the check'), _('Re-run check')
            header = f'<style>a {{text-decoration: none}}</style><h2>{header} [{self.items.currentRow()+1} / {self.items.count()}]</h2>'
            msg = '<p>%s</p>'
            footer = '<div>%s<a href="fix:errors" title="%s">%s</a><br><br> <a href="run:check" title="%s">%s</a></div>'
            if err.has_multiple_locations:
                activate = []
                for i, (name, lnum, col) in enumerate(err.all_locations):
                    activate.append(f'<a href="activate:item:{i}" title="{open_tt}">{name} {loc_to_string(lnum, col)}</a>')
                many = len(activate) > 2
                activate = '<div>{}</div>'.format('<br>'.join(activate))
                if many:
                    activate += '<br>'
                activate = activate.replace('%', '%%')
                template = header + ((msg + activate) if many else (activate + msg)) + footer
            else:
                activate = f'<div><a href="activate:item" title="{open_tt}">{err.name} {loc}</a></div>'
                activate = activate.replace('%', '%%')
                template = header + activate + msg + footer
            self.help.setText(
                template % (err.HELP, ifix, fix_tt, fix_msg, run_tt, run_msg))

    def run_checks(self, container):
        with BusyCursor():
            self.show_busy()
            QApplication.processEvents()
            errors = run_checks(container)
            self.hide_busy()

        for err in sorted(errors, key=lambda e:(100 - e.level, e.name)):
            i = QListWidgetItem(build_error_message(err), self.items)
            i.setData(Qt.ItemDataRole.UserRole, err)
            i.setIcon(icon_for_level(err.level))
        if errors:
            self.items.setCurrentRow(0)
            self.current_item_changed()
            self.items.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.clear_help()

    def fix_errors(self, container, errors):
        with BusyCursor():
            self.show_busy(_('Running fixers, please wait...'))
            QApplication.processEvents()
            changed = fix_errors(container, errors)
        self.run_checks(container)
        return changed

    def show_busy(self, msg=_('Running checks, please wait...')):
        self.help.setText(msg)
        self.items.clear()

    def hide_busy(self):
        self.help.setText('')
        self.items.clear()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.current_item_activated()
        return super().keyPressEvent(ev)

    def clear(self):
        self.items.clear()
        self.clear_help()


def main():
    from calibre.gui2 import Application
    from calibre.gui2.tweak_book.boss import get_container
    app = Application([])  # noqa: F841
    path = sys.argv[-1]
    container = get_container(path)
    d = Check()
    d.run_checks(container)
    d.show()
    app.exec()


if __name__ == '__main__':
    main()
