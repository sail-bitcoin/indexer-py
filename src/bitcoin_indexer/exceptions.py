class RpcHTTPStatusError(Exception):
    """Custom Exception class to handle rpc errors"""

    def __init__(self, status_code: int, reason: str, method: str, params: list | None = None):
        self.status_code = status_code
        self.reason = reason
        self.method = method
        self.params = params
        super().__init__(f"RPC call failed: {status_code} {reason} - {method} {params}")


class BitcoinRpcError(Exception):
    """Custom Exception class to handle Bitcoin RPC error codes"""

    def __init__(self, method: str, params: list | None = None, code: int = 0, message: str = ""):
        self.method = method
        self.params = params
        self.code = code
        self.message = message
        super().__init__(f"Bitcoin RPC return an error: {code} {message} - {method} {params}")
