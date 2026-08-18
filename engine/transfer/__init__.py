# engine/transfer — NearShare v1 transfer orchestration.
#
# High-level orchestrators:
#   sender.py         — TransferSender (full async send flow)
#   receiver.py       — TransferReceiver (full async receive flow)
#
# Supporting modules:
#   session.py        — TransferSession state and ack tracking
#   chunker.py        — split() generator + ChunkAssembler
#   ack.py            — AckBitmap / AckTracker + ACK wire helpers
#   window.py         — Sliding-window send-side back-pressure
#   result.py         — TransferResult frozen outcome dataclass
#   progress.py       — ProgressTracker + ProgressSnapshot
#   retransmit.py     — Missing-chunk retransmission controller
#   resume.py         — ResumeToken + ResumeManager
#   state_machine.py  — TransferStateMachine legal transitions
#   builder.py        — TransferSessionBuilder (fluent API)

from engine.transfer.sender import TransferSender
from engine.transfer.receiver import TransferReceiver
from engine.transfer.result import TransferResult

__all__ = [
    "TransferSender",
    "TransferReceiver",
    "TransferResult",
]
