"""Qwen2.5-Omni-3B as an ASR engine, for the Vietnamese-quality comparison.

This is the heaviest and least streaming-friendly of the three candidates:
Omni is a generative multimodal model, not a purpose-built streaming ASR
model, so there's no cheap incremental re-decode — we only call it on
VAD-finalized segments, not on every partial tick (see main.py).

Needs torch + transformers + accelerate + qwen-omni-utils, which live in
requirements-qwen.txt (opt-in, not the default install) so picking "erax" or
"whisper" never forces a multi-GB torch download. Imports are deferred into
load() so the rest of the service works fine without this extra installed.
"""

from __future__ import annotations

import logging

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

TRANSCRIBE_PROMPT_VI = (
    "Hãy phiên âm chính xác đoạn âm thanh sau sang văn bản tiếng Việt. "
    "Chỉ trả về đúng nội dung được nói, không thêm bình luận hay giải thích. "
    "TUYỆT ĐỐI không mở đầu bằng các câu dẫn kiểu 'Đoạn âm thanh được phiên âm "
    "là:' hay tương tự — chỉ in ra transcript, không có gì khác."
)


class QwenOmniEngine:
    def __init__(self, name: str, model_id: str):
        self.name = name
        self.model_id = model_id
        self._model = None
        self._processor = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Qwen2.5-Omni engine needs torch/transformers/torchvision. "
                "Install codebase/stt-service/requirements-qwen.txt in this venv first."
            ) from exc

        self._torch = torch
        self._processor = Qwen2_5OmniProcessor.from_pretrained(self.model_id)
        self._model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
            self.model_id,
            torch_dtype=torch.bfloat16,
            device_map=settings.ASR_DEVICE,
        )
        self._model.eval()
        logger.info("Loaded Qwen2.5-Omni engine: %s", self.model_id)

    def unload(self) -> None:
        self._model = None
        self._processor = None
        try:
            self._torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def transcribe(self, pcm16: bytes, language: str = "vi") -> str:
        if self._model is None or self._processor is None:
            raise RuntimeError(f"{self.name} engine used before load()")
        if len(pcm16) < settings.SAMPLE_RATE * 0.2:  # <200ms
            return ""

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": audio},
                    {"type": "text", "text": TRANSCRIBE_PROMPT_VI},
                ],
            }
        ]
        text_prompt = self._processor.apply_chat_template(
            conversation, add_generation_prompt=True, tokenize=False
        )
        inputs = self._processor(
            text=text_prompt,
            audio=[audio],
            sampling_rate=settings.SAMPLE_RATE,
            return_tensors="pt",
        ).to(self._model.device)

        with self._torch.no_grad():
            output_ids = self._model.generate(**inputs, max_new_tokens=256, return_audio=False)
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        text = self._processor.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )[0]
        return text.strip()
