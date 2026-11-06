"""Run the tests under `tests` and write a record of the run for the verifier.

Run as `python3 -I runner.py <fd>` from the workspace root. The record goes to
the inherited file descriptor `fd` once the run ends, so a process that exits
early leaves none.
"""

import json
import os
import sys
import unittest


class RecordingResult(unittest.TextTestResult):
    passed: list[str]

    def startTestRun(self) -> None:
        super().startTestRun()
        self.passed = []

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.append(test.id())


class RecordingRunner(unittest.TextTestRunner):
    # unittest documents this override, while typeshed rejects a subclass of
    # `TextTestResult` as `resultclass`.
    def _makeResult(self) -> RecordingResult:
        return RecordingResult(
            self.stream, self.descriptions, self.verbosity, durations=self.durations
        )


def main() -> None:
    record = os.fdopen(int(sys.argv[1]), 'w')

    suite = unittest.TestLoader().discover('tests', top_level_dir='.')
    result = RecordingRunner().run(suite)

    assert isinstance(result, RecordingResult)
    json.dump({'passed': result.passed, 'successful': result.wasSuccessful()}, record)
    record.close()


if __name__ == '__main__':
    main()
