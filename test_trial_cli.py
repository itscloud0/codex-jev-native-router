import tempfile
import unittest
from pathlib import Path
from unittest import mock

import manage


class TrialCliTests(unittest.TestCase):
    def test_registration_dispatches_without_changing_execution(self):
        root = Path("/unused-install")
        options = ["start", "thread", "--kind", "routine", "--scope", "component",
                   "--risk", "low", "--uncertainty", "known", "--effort", "medium"]
        with mock.patch("trials.start", return_value={"applied": False}) as start:
            result = manage.trial_command(options, root)
        self.assertFalse(result["applied"])
        start.assert_called_once_with(root, "thread", project=Path.cwd(), kind="routine",
                                      scope="component", risk="low", uncertainty="known", effort="medium")

    def test_invalid_options_do_not_register_or_expose_values(self):
        for args in ([], ["start", "private-value"], ["unknown-private"],
                     ["finish", "private-value", "--outcome", "accepted", "--outcome", "failed"],
                     ["report", "--hours", "private-value"], ["report", "--hours", "721"]):
            with self.subTest(args=args), mock.patch("trials.start") as start:
                with self.assertRaises(ValueError) as error:
                    manage.trial_command(args)
                self.assertNotIn("private-value", str(error.exception))
                self.assertNotIn("unknown-private", str(error.exception))
                start.assert_not_called()

    def test_finish_and_reopen_dispatch(self):
        root = Path("/unused-install")
        with mock.patch("trials.finish", return_value={}) as finish:
            manage.trial_command(["finish", "task", "--outcome", "failed", "--checks", "not_run"], root)
        finish.assert_called_once_with(root, "task", outcome="failed", checks="not_run")
        with mock.patch("trials.reopen", return_value={}) as reopen:
            manage.trial_command(["reopen", "task"], root)
        reopen.assert_called_once_with(root, "task")

    def test_report_reads_empty_local_ledger_without_model_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("manage.telemetry_rows", return_value=iter([])) as rows:
                result = manage.trial_command(["report", "--hours", "24"], root)
            self.assertEqual(result["tasks"], [])
            self.assertIsNone(result["subscription_allowance_savings"])
            self.assertFalse((root / "state/trials.json").exists())
            self.assertEqual(rows.call_args.args[0], root)


if __name__ == "__main__":
    unittest.main()
