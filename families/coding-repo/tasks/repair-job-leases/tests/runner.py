"""Run the tests under `tests` and write a record of the run for the verifier.

Run as `python3 -I runner.py <fd>` from the workspace root. The record goes to
the inherited file descriptor `fd` once the run ends, so a process that exits
early leaves none. It notes whether the `unittest` machinery the run went
through still matches its state from before the tested code loaded.
"""

import importlib
import json
import os
import sys
import unittest


type Namespaces = dict[object, dict[str, object]]


class RecordingResult(unittest.TextTestResult):
    passed: list[str]

    def startTestRun(self) -> None:
        super().startTestRun()
        self.passed = []

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.append(test.id())


class RecordingRunner(unittest.TextTestRunner):
    def _makeResult(self) -> RecordingResult:
        return RecordingResult(self.stream, self.descriptions, self.verbosity)


def namespaces() -> Namespaces:
    """Copy the namespace of each `unittest` module and class and of this runner.

    The submodules that `unittest` loads only on use are loaded first.
    """
    for submodule in ('unittest.async_case', 'unittest.mock'):
        importlib.import_module(submodule)
    copies: Namespaces = {
        owner: dict(vars(owner)) for owner in (RecordingResult, RecordingRunner)
    }
    for name, module in list(sys.modules.items()):
        if name != 'unittest' and not name.startswith('unittest.'):
            continue
        copies[module] = dict(vars(module))
        for value in vars(module).values():
            if isinstance(value, type) and value.__module__ == name:
                copies[value] = dict(vars(value))
    return copies


def intact(before: Namespaces) -> bool:
    """Tell whether no name in `before` was removed or rebound since.

    A class may not gain names either, while a module may gain a submodule.
    """
    for owner, names in before.items():
        current = vars(owner)
        if isinstance(owner, type) and current.keys() != names.keys():
            return False
        if any(current.get(name, owner) is not value for name, value in names.items()):
            return False
    return True


def main() -> None:
    record = os.fdopen(int(sys.argv[1]), 'w')
    before = namespaces()

    suite = unittest.TestLoader().discover('tests', top_level_dir='.')
    result = RecordingRunner().run(suite)

    assert isinstance(result, RecordingResult)
    json.dump(
        {
            'intact': intact(before),
            'passed': result.passed,
            'successful': result.wasSuccessful(),
        },
        record,
    )
    record.close()


if __name__ == '__main__':
    main()
