from __future__ import absolute_import, print_function

import platform
import random

from itertools import count
from time import time, sleep

from kombu.utils.compat import OrderedDict

from celery import group, VERSION_BANNER
from celery.exceptions import TimeoutError
from celery.five import range, values
from celery.utils.debug import blockdetection
from celery.utils.text import pluralize
from celery.utils.timeutils import humanize_seconds

from .app import (
    marker, add, any_, kill, sleeping,
    sleeping_ignore_limits, segfault,
)
from .data import BIG, SMALL

BANNER = """\
Celery stress-suite v{version}

{platform}

[config]
.> broker: {conninfo}

[toc: {total} {TESTS} total]
{toc}
"""


class Suite(object):

    def __init__(self, app, block_timeout=30 * 60):
        self.app = app
        self.connerrors = self.app.connection().recoverable_connection_errors
        self.block_timeout = block_timeout

        self.tests = OrderedDict(
            (fun.__name__, fun) for fun in [
                self.manyshort,
                self.termbysig,
                self.bigtasks,
                self.smalltasks,
                self.timelimits,
                self.timelimits_soft,
                self.revoketermfast,
                self.revoketermslow,
                self.alwayskilled,
            ]
        )

    def run(self, names=None, iterations=50, offset=0,
            numtests=None, list_all=False, repeat=0, **kw):
        tests = self.filtertests(names)[offset:numtests or None]
        if list_all:
            return print(self.testlist(tests))
        print(self.banner(tests))
        it = count() if repeat == float('Inf') else range(int(repeat) + 1)
        for i in it:
            marker(
                'Stresstest suite start (repetition {0})'.format(i + 1),
                '+',
            )
            for j, test in enumerate(tests):
                self.runtest(test, iterations, j + 1)
            marker(
                'Stresstest suite end (repetition {0})'.format(i + 1),
                '+',
            )

    def filtertests(self, names):
        try:
            return ([self.tests[n] for n in names] if names
                    else list(values(self.tests)))
        except KeyError as exc:
            raise KeyError('Unknown test name: {0}'.format(exc))

    def testlist(self, tests):
        return ',\n'.join(
            '.> {0}) {1}'.format(i + 1, t.__name__)
            for i, t in enumerate(tests)
        )

    def banner(self, tests):
        app = self.app
        return BANNER.format(
            app='{0}:0x{1:x}'.format(app.main or '__main__', id(app)),
            version=VERSION_BANNER,
            conninfo=app.connection().as_uri(),
            platform=platform.platform(),
            toc=self.testlist(tests),
            TESTS=pluralize(len(tests), 'test'),
            total=len(tests),
        )

    def manyshort(self):
        self.join(group(add.s(i, i) for i in xrange(1000))(), propagate=True)

    def runtest(self, fun, n=50, index=0):
        with blockdetection(self.block_timeout):
            t = time()
            i = 0
            failed = False
            marker('{0}: {1}({2})'.format(index, fun.__name__, n))
            try:
                for i in range(n):
                    print('{0} ({1})'.format(i, fun.__name__), end=' ')
                    try:
                        fun()
                        print('-> done')
                    except Exception as exc:
                        print('-> {}'.format(exc))
            except Exception:
                failed = True
                raise
            finally:
                print('{0} {1} iterations in {2}s'.format(
                    'failed after' if failed else 'completed',
                    i + 1, humanize_seconds(time() - t),
                ))

    def termbysig(self):
        self._evil_groupmember(kill)

    def termbysegfault(self):
        self._evil_groupmember(segfault)

    def timelimits(self):
        self._evil_groupmember(sleeping, 2, timeout=1)

    def timelimits_soft(self):
        self._evil_groupmember(sleeping_ignore_limits, 2,
                               soft_timeout=1, timeout=1.1)

    def alwayskilled(self):
        g = group(kill.s() for _ in range(10))
        self.join(g(), timeout=10)

    def _evil_groupmember(self, evil_t, *eargs, **opts):
        g1 = group(add.s(2, 2).set(**opts), evil_t.s(*eargs).set(**opts),
                   add.s(4, 4).set(**opts), add.s(8, 8).set(**opts))
        g2 = group(add.s(3, 3).set(**opts), add.s(5, 5).set(**opts),
                   evil_t.s(*eargs).set(**opts), add.s(7, 7).set(**opts))
        self.join(g1(), timeout=10)
        self.join(g2(), timeout=10)

    def bigtasks(self, wait=None):
        self._revoketerm(wait, False, False, BIG)

    def smalltasks(self, wait=None):
        self._revoketerm(wait, False, False, SMALL)

    def revoketermfast(self, wait=None):
        self._revoketerm(wait, True, False, SMALL)

    def revoketermslow(self, wait=5):
        self._revoketerm(wait, True, True, BIG)

    def _revoketerm(self, wait=None, terminate=True,
                    joindelay=True, data=BIG):
        g = group(any_.s(data, sleep=wait) for i in range(8))
        r = g()
        if terminate:
            if joindelay:
                sleep(random.choice(range(4)))
            r.revoke(terminate=True)
        self.join(r, timeout=5)

    def join(self, r, propagate=False, **kwargs):
        while 1:
            try:
                return r.get(propagate=propagate, **kwargs)
            except TimeoutError as exc:
                marker('join timed out: {0!r}'.format(exc), '!')
            except self.connerrors as exc:
                marker('join: connection lost: {0!r}'.format(exc), '!')
