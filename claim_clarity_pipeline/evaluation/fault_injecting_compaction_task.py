"""The compaction task, killed after it wrote its first rewritten file and before it committed it. Used by the
tests (tests/test_claim_clarity_pipeline.py) to show that a crash leaves only an unreferenced file."""
import logging
import os

from .. import compaction_task


def main():
    def die_before_the_commit(*arguments, **keywords):
        logging.getLogger(__name__).error("injected fault: task dies between the rewrite and its commit")
        os._exit(137)
    compaction_task.replace_data_files = die_before_the_commit
    compaction_task.main()


if __name__ == "__main__":
    main()
