import io
import unittest

import cli_chat


class ClientEventTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(cli_chat.CodexClient)
        self.client.stdin = io.StringIO()
        self.client.stdout = io.StringIO()
        self.client.stderr = io.StringIO()
        self.client.streamed = set()
        self.client.turn_done = False
        self.client.turn_status = "unknown"
        self.sent = []
        self.client.send = self.sent.append

    def test_stream_and_completion_do_not_duplicate_agent_text(self):
        self.client._event({"method": "item/agentMessage/delta", "params": {
            "itemId": "m", "delta": "OK"}})
        self.client._event({"method": "item/completed", "params": {
            "item": {"id": "m", "type": "agentMessage", "text": "OK"}}})
        self.client._event({"method": "turn/completed", "params": {
            "turn": {"status": "completed"}}})
        self.assertEqual(self.client.stdout.getvalue(), "OK\n")
        self.assertTrue(self.client.turn_done)

    def test_noninteractive_approval_fails_closed(self):
        self.client._event({"id": 7, "method": "item/commandExecution/requestApproval",
                            "params": {"command": "echo example"}})
        self.assertEqual(self.sent, [{"id": 7, "result": {"decision": "decline"}}])
        self.client._event({"id": 8, "method": "mcpServer/elicitation/request", "params": {}})
        self.assertEqual(self.sent[-1]["error"]["code"], -32601)

    def test_model_validation(self):
        self.assertTrue(cli_chat.MODELS.fullmatch("jev-auto"))
        self.assertTrue(cli_chat.MODELS.fullmatch("gpt-6-sol"))
        self.assertFalse(cli_chat.MODELS.fullmatch("bad;command"))

    def test_last_resumes_thread_before_turn(self):
        thread = "01a0e77c-b9c3-7961-8201-79edce3ffc49"
        self.client.model = "jev-auto"
        self.client.request = lambda method, params: (
            {"data": [{"id": thread}]} if method == "thread/list" else
            {"thread": {"id": thread}} if method == "thread/resume" else {})
        self.assertEqual(self.client.initialize(last=True), thread)
        self.assertEqual(self.client.thread_id, thread)
        self.assertEqual(self.sent[-1], {"method": "initialized", "params": {}})


if __name__ == "__main__":
    unittest.main()
