from __future__ import annotations

import json
import logging
import socket
from typing import Any, Mapping


class BlenderConnection:
    """Socket client for Blender addon commands."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        logger: logging.Logger | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self.logger = logger
        self.timeout_seconds = timeout_seconds

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            if self.logger:
                self.logger.info("Connected to Blender at %s:%s", self.host, self.port)
            return True
        except Exception as exc:
            if self.logger:
                self.logger.error("Failed to connect to Blender: %s", exc)
            self.sock = None
            return False

    def disconnect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception as exc:
                if self.logger:
                    self.logger.error("Error disconnecting from Blender: %s", exc)
            finally:
                self.sock = None

    def receive_full_response(self, buffer_size: int = 8192) -> bytes:
        if not self.sock:
            raise ConnectionError("Not connected to Blender")
        chunks: list[bytes] = []
        self.sock.settimeout(self.timeout_seconds)
        try:
            while True:
                try:
                    chunk = self.sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break
                    chunks.append(chunk)
                    data = b"".join(chunks)
                    try:
                        json.loads(data.decode("utf-8"))
                        return data
                    except json.JSONDecodeError:
                        continue
                except socket.timeout:
                    break
        except Exception as exc:
            if self.logger:
                self.logger.error("Error during receive: %s", exc)
            raise

        if chunks:
            data = b"".join(chunks)
            try:
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError as exc:
                raise Exception("Incomplete JSON response received") from exc
        raise Exception("No data received")

    def send_command(self, command_type: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {"type": command_type, "params": dict(params or {})}
        try:
            if self.logger:
                self.logger.info("Sending command: %s", command_type)

            self.sock.sendall(json.dumps(command).encode("utf-8"))
            response_data = self.receive_full_response()
            response = json.loads(response_data.decode("utf-8"))

            if response.get("status") == "error":
                if self.logger:
                    self.logger.error("Blender error: %s", response.get("message"))
                raise Exception(response.get("message", "Unknown error from Blender"))
            return response.get("result", {})
        except socket.timeout:
            if self.logger:
                self.logger.error("Socket timeout while waiting for response from Blender")
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as exc:
            if self.logger:
                self.logger.error("Socket connection error: %s", exc)
            self.sock = None
            raise Exception(f"Connection to Blender lost: {exc}")
        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.error("Invalid JSON response from Blender: %s", exc)
            self.sock = None
            raise Exception(f"Invalid response from Blender: {exc}")
        except Exception as exc:
            if self.logger:
                self.logger.error("Error communicating with Blender: %s", exc)
            self.sock = None
            raise Exception(f"Communication error with Blender: {exc}")
