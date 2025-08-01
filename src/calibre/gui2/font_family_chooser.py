#!/usr/bin/env python


__license__   = 'GPL v3'
__copyright__ = '2012, Kovid Goyal <kovid at kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import os
import shutil

from qt.core import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFont,
    QFontComboBox,
    QFontDatabase,
    QFontInfo,
    QFontMetrics,
    QGridLayout,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLineEdit,
    QListView,
    QPen,
    QPushButton,
    QRawFont,
    QSize,
    QSizePolicy,
    QStringListModel,
    QStyle,
    QStyledItemDelegate,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

from calibre.constants import config_dir
from calibre.gui2 import choose_files, empty_index, error_dialog, info_dialog
from calibre.utils.icu import lower as icu_lower


def add_fonts(parent):
    files = choose_files(parent, 'add fonts to calibre',
            _('Select font files'), filters=[(_('TrueType/OpenType Fonts'),
                ['ttf', 'otf', 'woff', 'woff2'])], all_files=False)
    if not files:
        return
    families = set()
    for f in files:
        r = QRawFont()
        r.loadFromFile(f, 11.0, QFont.HintingPreference.PreferDefaultHinting)
        if r.isValid():
            families.add(r.familyName())
        else:
            error_dialog(parent, _('Corrupt font'),
                    _('Failed to load font from the file: {}').format(f), show=True)
            return
    families = sorted(families)

    dest = os.path.join(config_dir, 'fonts')
    for f in files:
        shutil.copyfile(f, os.path.join(dest, os.path.basename(f)))

    return families


def writing_system_for_font(font):
    has_latin = True
    systems = QFontDatabase.writingSystems(font.family())

    # this just confuses the algorithm below. Vietnamese is Latin with lots of
    # special chars
    try:
        systems.remove(QFontDatabase.WritingSystem.Vietnamese)
    except ValueError:
        pass

    system = QFontDatabase.WritingSystem.Any

    if (QFontDatabase.WritingSystem.Latin not in systems):
        has_latin = False
        # we need to show something
        if systems:
            system = systems[-1]
    else:
        systems.remove(QFontDatabase.WritingSystem.Latin)

    if not systems:
        return system, has_latin

    if (len(systems) == 1 and systems[0].value > QFontDatabase.WritingSystem.Cyrillic.value):
        return systems[0], has_latin

    if (len(systems) <= 2 and
        systems[-1].value > QFontDatabase.WritingSystem.Armenian.value and
        systems[-1].value < QFontDatabase.WritingSystem.Vietnamese.value):
        return systems[-1], has_latin

    if (len(systems) <= 5 and
        systems[-1].value >= QFontDatabase.WritingSystem.SimplifiedChinese.value and
        systems[-1].value <= QFontDatabase.WritingSystem.Korean.value):
        system = systems[-1]

    return system, has_latin


class FontFamilyDelegate(QStyledItemDelegate):

    def sizeHint(self, option, index):
        try:
            return self.do_size_hint(option, index)
        except Exception:
            return QSize(300, 50)

    def do_size_hint(self, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ''
        font = QFont(option.font)
        font.setPointSizeF(QFontInfo(font).pointSize() * 1.5)
        m = QFontMetrics(font)
        return QSize(m.width(text), m.height())

    def paint(self, painter, option, index):
        QStyledItemDelegate.paint(self, painter, option, empty_index)
        painter.save()
        try:
            self.do_paint(painter, option, index)
        except Exception:
            import traceback
            traceback.print_exc()
        painter.restore()

    def do_paint(self, painter, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        font = QFont(option.font)
        font.setPointSizeF(QFontInfo(font).pointSize() * 1.5)
        font2 = QFont(font)
        font2.setFamily(text)

        system, has_latin = writing_system_for_font(font2)
        if has_latin:
            font = font2

        r = option.rect
        color = option.palette.text()

        if option.state & QStyle.StateFlag.State_Selected:
            color = option.palette.highlightedText()
        painter.setPen(QPen(color, 0))

        if (option.direction == Qt.LayoutDirection.RightToLeft):
            r.setRight(r.right() - 4)
        else:
            r.setLeft(r.left() + 4)

        painter.setFont(font)
        painter.drawText(r, Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeading|Qt.TextFlag.TextSingleLine, text)

        if (system != QFontDatabase.WritingSystem.Any):
            w = painter.fontMetrics().horizontalAdvance(text + '  ')
            painter.setFont(font2)
            sample = QFontDatabase.writingSystemSample(system)
            if (option.direction == Qt.LayoutDirection.RightToLeft):
                r.setRight(r.right() - w)
            else:
                r.setLeft(r.left() + w)
            painter.drawText(r, Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeading|Qt.TextFlag.TextSingleLine, sample)


class Typefaces(QLabel):

    def __init__(self, parent=None):
        QLabel.__init__(self, parent)
        self.setMinimumWidth(400)
        self.base_msg = '<h3>'+_('Choose a font family')+'</h3>'
        self.setText(self.base_msg)
        self.setWordWrap(True)

    def show_family(self, family, faces):
        if not family:
            self.setText(self.base_msg)
            return
        msg = '''
        <h3>%s</h3>
        <dl style="font-size: smaller">
        {0}
        </dl>
        '''%(_('Available faces for %s')%family)
        entries = []
        for font in faces:
            sf = (font['wws_subfamily_name'] or font['preferred_subfamily_name'] or
                  font['subfamily_name'])
            entries.append('''
            <dt><b>{sf}</b></dt>
            <dd>font-stretch: <i>{width}</i> font-weight: <i>{weight}</i> font-style:
            <i>{style}</i></dd>

            '''.format(sf=sf, width=font['font-stretch'],
                    weight=font['font-weight'], style=font['font-style']))
        msg = msg.format('\n\n'.join(entries))
        self.setText(msg)


class FontsView(QListView):

    changed = pyqtSignal()

    def __init__(self, parent):
        QListView.__init__(self, parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.d = FontFamilyDelegate(self)
        self.setItemDelegate(self.d)

    def currentChanged(self, current, previous):
        self.changed.emit()
        QListView.currentChanged(self, current, previous)


class FontFamilyDialog(QDialog):

    def __init__(self, current_family, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('Choose font family'))
        self.setWindowIcon(QIcon.ic('font.png'))
        from calibre.utils.fonts.scanner import font_scanner
        self.font_scanner = font_scanner

        self.m = QStringListModel(self)
        self.build_font_list()
        self.l = l = QGridLayout()
        self.setLayout(l)
        self.view = FontsView(self)
        self.view.setModel(self.m)
        self.view.setCurrentIndex(self.m.index(0))
        if current_family:
            for i, val in enumerate(self.families):
                if icu_lower(val) == icu_lower(current_family):
                    self.view.setCurrentIndex(self.m.index(i))
                    break
        self.view.doubleClicked.connect(self.accept, type=Qt.ConnectionType.QueuedConnection)
        self.view.changed.connect(self.current_changed,
                type=Qt.ConnectionType.QueuedConnection)
        self.faces = Typefaces(self)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        self.add_fonts_button = afb = self.bb.addButton(_('Add &fonts'),
                QDialogButtonBox.ButtonRole.ActionRole)
        afb.setIcon(QIcon.ic('plus.png'))
        afb.clicked.connect(self.add_fonts)
        self.ml = QLabel(_('Choose a font family from the list below:'))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(_('Search'))
        self.search.returnPressed.connect(self.find)
        self.nb = QToolButton(self)
        self.nb.setIcon(QIcon.ic('arrow-down.png'))
        self.nb.setToolTip(_('Find next'))
        self.pb = QToolButton(self)
        self.pb.setIcon(QIcon.ic('arrow-up.png'))
        self.pb.setToolTip(_('Find previous'))
        self.nb.clicked.connect(self.find_next)
        self.pb.clicked.connect(self.find_previous)

        l.addWidget(self.ml, 0, 0, 1, 4)
        l.addWidget(self.search, 1, 0, 1, 1)
        l.addWidget(self.nb, 1, 1, 1, 1)
        l.addWidget(self.pb, 1, 2, 1, 1)
        l.addWidget(self.view, 2, 0, 1, 3)
        l.addWidget(self.faces, 1, 3, 2, 1)
        l.addWidget(self.bb, 3, 0, 1, 4)
        l.setAlignment(self.faces, Qt.AlignmentFlag.AlignTop)

        self.resize(800, 600)

    def set_current(self, i):
        self.view.setCurrentIndex(self.m.index(i))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Return:
            return
        return QDialog.keyPressEvent(self, e)

    def find(self, backwards=False):
        i = self.view.currentIndex().row()
        if i < 0:
            i = 0
        q = icu_lower(str(self.search.text())).strip()
        if not q:
            return
        r = (range(i-1, -1, -1) if backwards else range(i+1,
            len(self.families)))
        for j in r:
            f = self.families[j]
            if q in icu_lower(f):
                self.set_current(j)
                return

    def find_next(self):
        self.find()

    def find_previous(self):
        self.find(backwards=True)

    def build_font_list(self):
        try:
            self.families = list(self.font_scanner.find_font_families())
        except Exception:
            self.families = []
            print('WARNING: Could not load fonts')
            import traceback
            traceback.print_exc()
        self.families.insert(0, _('None'))
        self.m.setStringList(self.families)

    def add_fonts(self):
        families = add_fonts(self)
        if not families:
            return
        self.font_scanner.do_scan()
        self.m.beginResetModel()
        self.build_font_list()
        self.m.endResetModel()
        self.view.setCurrentIndex(self.m.index(0))
        if families:
            for i, val in enumerate(self.families):
                if icu_lower(val) == icu_lower(families[0]):
                    self.view.setCurrentIndex(self.m.index(i))
                    break

        info_dialog(self, _('Added fonts'),
                _('Added font families: %s')%(
                    ', '.join(families)), show=True)

    @property
    def font_family(self):
        idx = self.view.currentIndex().row()
        if idx == 0:
            return None
        return self.families[idx]

    def current_changed(self):
        fam = self.font_family
        self.faces.show_family(fam, self.font_scanner.fonts_for_family(fam)
                if fam else None)


class FontFamilyChooser(QWidget):

    family_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.l = l = QHBoxLayout()
        l.setContentsMargins(0, 0, 0, 0)
        self.setLayout(l)
        self.button = QPushButton(self)
        self.button.setIcon(QIcon.ic('font.png'))
        self.button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        l.addWidget(self.button)
        self.default_text = _('Choose &font family')
        self.font_family = None
        self.button.clicked.connect(self.show_chooser)
        self.clear_button = QToolButton(self)
        self.clear_button.setIcon(QIcon.ic('clear_left.png'))
        self.clear_button.clicked.connect(self.clear_family)
        l.addWidget(self.clear_button)
        self.setToolTip = self.button.setToolTip
        self.toolTip = self.button.toolTip
        self.clear_button.setToolTip(_('Clear the font family'))
        l.addStretch(1)

    def clear_family(self):
        self.font_family = None

    @property
    def font_family(self):
        return self._current_family

    @font_family.setter
    def font_family(self, val):
        if not val:
            val = None
        self._current_family = val
        self.button.setText(val or self.default_text)
        self.family_changed.emit(val)

    def show_chooser(self):
        d = FontFamilyDialog(self.font_family, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.font_family = d.font_family


def test():
    from calibre.gui2 import Application
    app = Application([])
    app
    d = QDialog()
    d.setLayout(QVBoxLayout())
    d.layout().addWidget(FontFamilyChooser(d))
    d.layout().addWidget(QFontComboBox(d))
    d.exec()


if __name__ == '__main__':
    test()
