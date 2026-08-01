import unittest

from zxcode.mcp.errors import map_rpc_error
from zxcode.mcp.protocol import (
    INVALID_REQUEST,
    PARSE_ERROR,
    IdFactory,
    Notification,
    Request,
    Response,
    RpcError,
    RpcParseError,
    parse_json_line,
    parse_message,
)


class MessageParsingTests(unittest.TestCase):
    def test_request_round_trip(self):
        message = parse_message(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "x"}}
        )
        self.assertIsInstance(message, Request)
        self.assertEqual(message.id, 7)
        self.assertEqual(message.method, "tools/call")
        self.assertEqual(message.to_dict()["params"], {"name": "x"})

    def test_string_id_request(self):
        message = parse_message({"jsonrpc": "2.0", "id": "abc", "method": "ping"})
        self.assertIsInstance(message, Request)
        self.assertEqual(message.id, "abc")

    def test_response_with_result(self):
        message = parse_message({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        self.assertIsInstance(message, Response)
        self.assertFalse(message.is_error)
        self.assertEqual(message.result, {"tools": []})

    def test_response_with_error(self):
        message = parse_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "error": {"code": -32602, "message": "bad params", "data": {"k": 1}},
            }
        )
        self.assertIsInstance(message, Response)
        self.assertTrue(message.is_error)
        self.assertEqual(message.error.code, -32602)
        self.assertEqual(message.error.data, {"k": 1})

    def test_notification(self):
        message = parse_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsInstance(message, Notification)
        self.assertEqual(message.method, "notifications/initialized")

    def test_rejects_missing_version(self):
        with self.assertRaises(RpcParseError) as ctx:
            parse_message({"id": 1, "method": "ping"})
        self.assertEqual(ctx.exception.code, INVALID_REQUEST)

    def test_rejects_bad_id(self):
        with self.assertRaises(RpcParseError):
            parse_message({"jsonrpc": "2.0", "id": True, "method": "ping"})

    def test_rejects_response_with_both_result_and_error(self):
        with self.assertRaises(RpcParseError):
            parse_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {},
                    "error": {"code": -1, "message": "x"},
                }
            )

    def test_rejects_notification_without_method(self):
        with self.assertRaises(RpcParseError):
            parse_message({"jsonrpc": "2.0"})

    def test_rejects_params_that_are_not_object(self):
        with self.assertRaises(RpcParseError):
            parse_message({"jsonrpc": "2.0", "id": 1, "method": "x", "params": []})

    def test_rejects_error_without_message(self):
        with self.assertRaises(RpcParseError):
            parse_message({"jsonrpc": "2.0", "id": 1, "error": {"code": -1}})

    def test_parse_json_line_invalid_json(self):
        message, error = parse_json_line("{not json")
        self.assertIsNone(message)
        self.assertEqual(error.code, PARSE_ERROR)

    def test_parse_json_line_blank(self):
        self.assertEqual(parse_json_line("   \n"), (None, None))

    def test_parse_json_line_invalid_shape(self):
        message, error = parse_json_line('{"jsonrpc":"2.0","id":1}')
        self.assertIsNone(message)
        self.assertEqual(error.code, INVALID_REQUEST)


class IdFactoryTests(unittest.TestCase):
    def test_ids_are_monotonic(self):
        factory = IdFactory()
        self.assertEqual([factory.next(), factory.next(), factory.next()], [1, 2, 3])


class ErrorMappingTests(unittest.TestCase):
    def test_maps_standard_rpc_codes(self):
        self.assertEqual(map_rpc_error(-32700), "invalid_json")
        self.assertEqual(map_rpc_error(-32600), "invalid_request")
        self.assertEqual(map_rpc_error(-32601), "unknown_method")
        self.assertEqual(map_rpc_error(-32602), "invalid_arguments")

    def test_maps_unknown_code_to_remote_error(self):
        self.assertEqual(map_rpc_error(-32001), "remote_error")


if __name__ == "__main__":
    unittest.main()
