#!/usr/bin/env python


__license__ = 'GPL v3'
__copyright__ = '2014, Kovid Goyal <kovid at kovidgoyal.net>'

import atexit
import os
import sys
from collections import OrderedDict
from itertools import islice
from math import ceil
from operator import itemgetter
from threading import Lock, Thread
from unicodedata import normalize

from calibre import as_unicode
from calibre import detect_ncpus as cpu_count
from calibre.constants import filesystem_encoding
from calibre.utils.icu import lower as icu_lower
from calibre.utils.icu import primary_collator, primary_find, primary_sort_key
from calibre.utils.icu import upper as icu_upper
from polyglot.builtins import iteritems, itervalues
from polyglot.queue import Queue

DEFAULT_LEVEL1 = '/'
DEFAULT_LEVEL2 = '-_ 0123456789'
DEFAULT_LEVEL3 = '.'


class PluginFailed(RuntimeError):
    pass


class Worker(Thread):

    daemon = True

    def __init__(self, requests, results):
        Thread.__init__(self)
        self.requests, self.results = requests, results
        atexit.register(lambda: requests.put(None))

    def run(self):
        while True:
            x = self.requests.get()
            if x is None:
                break
            try:
                i, scorer, query = x
                self.results.put((True, (i, scorer(query))))
            except Exception as e:
                self.results.put((False, as_unicode(e)))
                # import traceback
                # traceback.print_exc()


wlock = Lock()
workers = []


def split(tasks, pool_size):
    '''
    Split a list into a list of sub lists, with the number of sub lists being
    no more than pool_size. Each sublist contains
    2-tuples of the form (i, x) where x is an element from the original list
    and i is the index of the element x in the original list.
    '''
    ans, count = [], 0
    delta = ceil(len(tasks) / pool_size)
    while tasks:
        section = [(count + i, task) for i, task in enumerate(tasks[:delta])]
        tasks = tasks[delta:]
        count += len(section)
        ans.append(section)
    return ans


def default_scorer(*args, **kwargs):
    try:
        return CScorer(*args, **kwargs)
    except PluginFailed:
        return PyScorer(*args, **kwargs)


class Matcher:

    def __init__(
        self,
        items,
        level1=DEFAULT_LEVEL1,
        level2=DEFAULT_LEVEL2,
        level3=DEFAULT_LEVEL3,
        scorer=None
    ):
        with wlock:
            if not workers:
                requests, results = Queue(), Queue()
                w = [Worker(requests, results) for i in range(max(1, cpu_count()))]
                [x.start() for x in w]
                workers.extend(w)
        items = (normalize('NFC', str(x)) for x in filter(None, items))
        self.items = items = tuple(items)
        tasks = split(items, len(workers))
        self.task_maps = [{j: i for j, (i, _) in enumerate(task)} for task in tasks]
        scorer = scorer or default_scorer
        self.scorers = [
            scorer(tuple(map(itemgetter(1), task_items))) for task_items in tasks
        ]
        self.sort_keys = None

    def __call__(self, query, limit=None):
        query = normalize('NFC', str(query))
        with wlock:
            for i, scorer in enumerate(self.scorers):
                workers[0].requests.put((i, scorer, query))
            if self.sort_keys is None:
                self.sort_keys = {
                    i: primary_sort_key(x)
                    for i, x in enumerate(self.items)
                }
            num = len(self.task_maps)
            scores, positions = {}, {}
            error = None
            while num > 0:
                ok, x = workers[0].results.get()
                num -= 1
                if ok:
                    task_num, vals = x
                    task_map = self.task_maps[task_num]
                    for i, (score, pos) in enumerate(vals):
                        item = task_map[i]
                        scores[item] = score
                        positions[item] = pos
                else:
                    error = x

        if error is not None:
            raise Exception(f'Failed to score items: {error}')
        items = sorted(((-scores[i], item, positions[i])
                        for i, item in enumerate(self.items)),
                       key=itemgetter(0))
        if limit is not None:
            del items[limit:]
        return OrderedDict(x[1:] for x in filter(itemgetter(0), items))


def get_items_from_dir(basedir, acceptq=lambda x: True):
    if isinstance(basedir, bytes):
        basedir = basedir.decode(filesystem_encoding)
    relsep = os.sep != '/'
    for dirpath, dirnames, filenames in os.walk(basedir):
        for f in filenames:
            x = os.path.join(dirpath, f)
            if acceptq(x):
                x = os.path.relpath(x, basedir)
                if relsep:
                    x = x.replace(os.sep, '/')
                yield x


class FilesystemMatcher(Matcher):

    def __init__(self, basedir, *args, **kwargs):
        Matcher.__init__(self, get_items_from_dir(basedir), *args, **kwargs)


# Python implementation of the scoring algorithm {{{

def calc_score_for_char(ctx, prev, current, distance):
    factor = 1.0
    ans = ctx.max_score_per_char

    if prev in ctx.level1:
        factor = 0.9
    elif prev in ctx.level2 or (
        icu_lower(prev) == prev and icu_upper(current) == current
    ):
        factor = 0.8
    elif prev in ctx.level3:
        factor = 0.7
    else:
        factor = (1.0 / distance) * 0.75

    return ans * factor


def process_item(ctx, haystack, needle):
    # non-recursive implementation using a stack
    stack = [(0, 0, 0, 0, [-1] * len(needle))]
    final_score, final_positions = stack[0][-2:]
    push, pop = stack.append, stack.pop
    while stack:
        hidx, nidx, last_idx, score, positions = pop()
        key = (hidx, nidx, last_idx)
        mem = ctx.memory.get(key, None)
        if mem is None:
            for i in range(nidx, len(needle)):
                n = needle[i]
                if (len(haystack) - hidx < len(needle) - i):
                    score = 0
                    break
                pos = primary_find(n, haystack[hidx:])[0]
                if pos == -1:
                    score = 0
                    break
                pos += hidx

                distance = pos - last_idx
                score_for_char = ctx.max_score_per_char if distance <= 1 else calc_score_for_char(
                    ctx, haystack[pos - 1], haystack[pos], distance
                )
                hidx = pos + 1
                push((hidx, i, last_idx, score, list(positions)))
                last_idx = positions[i] = pos
                score += score_for_char
            ctx.memory[key] = (score, positions)
        else:
            score, positions = mem
        if score > final_score:
            final_score = score
            final_positions = positions
    return final_score, final_positions


class PyScorer:
    __slots__ = (
        'items',
        'level1',
        'level2',
        'level3',
        'max_score_per_char',
        'memory',
    )

    def __init__(
        self,
        items,
        level1=DEFAULT_LEVEL1,
        level2=DEFAULT_LEVEL2,
        level3=DEFAULT_LEVEL3
    ):
        self.level1, self.level2, self.level3 = level1, level2, level3
        self.max_score_per_char = 0
        self.items = items

    def __call__(self, needle):
        for item in self.items:
            self.max_score_per_char = (1.0 / len(item) + 1.0 / len(needle)) / 2.0
            self.memory = {}
            yield process_item(self, item, needle)

# }}}


class CScorer:

    def __init__(
        self,
        items,
        level1=DEFAULT_LEVEL1,
        level2=DEFAULT_LEVEL2,
        level3=DEFAULT_LEVEL3
    ):
        from calibre_extensions.matcher import Matcher
        self.m = Matcher(
            items,
            primary_collator().capsule,
            str(level1), str(level2), str(level3)
        )

    def __call__(self, query):
        scores, positions = self.m.calculate_scores(query)
        yield from zip(scores, positions)


def test(return_tests=False):
    is_sanitized = 'libasan' in os.environ.get('LD_PRELOAD', '')
    import unittest

    class Test(unittest.TestCase):

        @unittest.skipIf(is_sanitized, "Sanitizer enabled can't check for leaks")
        def test_mem_leaks(self):
            import gc

            from calibre.utils.mem import get_memory as memory
            m = Matcher(['a'], scorer=CScorer)
            m('a')

            def doit(c):
                m = Matcher([
                    c + 'im/one.gif',
                    c + 'im/two.gif',
                    c + 'text/one.html',
                ],
                            scorer=CScorer)
                m('one')

            start = memory()
            for i in range(10):
                doit(str(i))
            gc.collect()
            used10 = memory() - start
            start = memory()
            for i in range(100):
                doit(str(i))
            gc.collect()
            used100 = memory() - start
            if used100 > 0 and used10 > 0:
                self.assertLessEqual(used100, 2 * used10)

        def test_non_bmp(self):
            raw = '_\U0001f431-'
            m = Matcher([raw], scorer=CScorer)
            positions = next(itervalues(m(raw)))
            self.assertEqual(
                positions, (0, 1, 2)
            )

    if return_tests:
        return unittest.TestLoader().loadTestsFromTestCase(Test)

    class TestRunner(unittest.main):

        def createTests(self):
            tl = unittest.TestLoader()
            self.test = tl.loadTestsFromTestCase(Test)

    TestRunner(verbosity=4)


def get_char(string, pos):
    return string[pos]


def input_unicode(prompt):
    ans = input(prompt)
    if isinstance(ans, bytes):
        ans = ans.decode(sys.stdin.encoding)
    return ans


def main(basedir=None, query=None):
    from calibre import prints
    from calibre.utils.terminal import ColoredStream
    if basedir is None:
        try:
            basedir = input_unicode(f'Enter directory to scan [{os.getcwd()}]: '
                                ).strip() or os.getcwd()
        except (EOFError, KeyboardInterrupt):
            return
    m = FilesystemMatcher(basedir)
    emph = ColoredStream(sys.stdout, fg='red', bold=True)
    while True:
        if query is None:
            try:
                query = input_unicode('Enter query: ')
            except (EOFError, KeyboardInterrupt):
                break
            if not query:
                break
        for path, positions in islice(iteritems(m(query)), 0, 10):
            positions = list(positions)
            p = 0
            while positions:
                pos = positions.pop(0)
                if pos == -1:
                    continue
                prints(path[p:pos], end='')
                ch = get_char(path, pos)
                with emph:
                    prints(ch, end='')
                p = pos + len(ch)
            prints(path[p:])
        query = None


if __name__ == '__main__':
    # main(basedir='/t', query='ns')
    # test()
    main()
