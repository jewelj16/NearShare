"""Tests for the UDP discovery adapter against localhost."""

import asyncio
import json
import socket
import struct
import time

import pytest

from desktop.adapters.udp_discovery import UdpDiscovery, _MULTICAST_GROUP
from engine.types import DeviceId, PeerInfo, PROTOCOL_VERSION


# Helpers

def _make_beacon(device_id: str, display_name: str, tcp_port: int = 47321) -> bytes:
    return json.dumps({
        "proto_version": PROTOCOL_VERSION,
        "device_id": device_id,
        "display_name": display_name,
        "tcp_port": tcp_port,
    }).encode("utf-8")


# Unit tests for beacon parsing

class TestBeaconParsing:
    """Test beacon encode/decode without network I/O."""

    def test_build_beacon_is_valid_json(self) -> None:
        disc = UdpDiscovery(
            device_id=DeviceId("test-id"),
            display_name="TestDevice",
        )
        beacon = disc._build_beacon()
        data = json.loads(beacon)
        assert data["device_id"] == "test-id"
        assert data["display_name"] == "TestDevice"
        assert data["proto_version"] == PROTOCOL_VERSION

    def test_parse_valid_beacon(self) -> None:
        beacon = _make_beacon("peer-1", "Peer One", 12345)
        peer = UdpDiscovery._parse_beacon(beacon)
        assert peer is not None
        assert peer.device_id == "peer-1"
        assert peer.display_name == "Peer One"
        assert peer.port == 12345

    def test_parse_invalid_json(self) -> None:
        assert UdpDiscovery._parse_beacon(b"not json") is None

    def test_parse_missing_fields(self) -> None:
        beacon = json.dumps({"proto_version": 1}).encode()
        assert UdpDiscovery._parse_beacon(beacon) is None

    def test_parse_bad_encoding(self) -> None:
        assert UdpDiscovery._parse_beacon(b"\xff\xfe") is None


# Peer filtering

class TestPeerFiltering:
    """Liveness timeout filtering."""

    def test_fresh_peer_visible(self) -> None:
        disc = UdpDiscovery(
            device_id=DeviceId("self"),
            display_name="Self",
        )
        peer = PeerInfo(
            device_id=DeviceId("other"),
            display_name="Other",
            ip="10.0.0.1",
            port=47321,
            last_seen=time.monotonic(),
        )
        disc._peers["other"] = peer
        assert len(disc.peers()) == 1

    def test_stale_peer_filtered(self) -> None:
        disc = UdpDiscovery(
            device_id=DeviceId("self"),
            display_name="Self",
            timeout=5.0,
        )
        peer = PeerInfo(
            device_id=DeviceId("old"),
            display_name="Old",
            ip="10.0.0.2",
            port=47321,
            last_seen=time.monotonic() - 10.0,
        )
        disc._peers["old"] = peer
        assert len(disc.peers()) == 0

    def test_empty_peers_list(self) -> None:
        disc = UdpDiscovery(
            device_id=DeviceId("self"),
            display_name="Self",
        )
        assert disc.peers() == []
