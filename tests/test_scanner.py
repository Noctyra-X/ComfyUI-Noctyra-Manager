"""结构分类纯函数测试（_classify_safetensors_keys，不涉及 IO）。

重点覆盖新增的 4 个 CivitAI 对齐类型：text_encoder / clip_vision / motion / detection，
以及确保它们不与 checkpoint/unet/vae/lora 相互误判。
"""
import pytest

from manager.scanner import _classify_safetensors_keys, classify_lora_subtype


def test_empty_returns_blank():
    assert _classify_safetensors_keys([]) == ""


def test_lora_kohya():
    assert _classify_safetensors_keys(["lora_unet_down_blocks_0.lora_up.weight"]) == "lora"


def test_lora_peft_new_format():
    # Flux/Qwen PEFT 新格式 .lora_A./.lora_B.
    assert _classify_safetensors_keys(["transformer.blocks.0.attn.lora_A.weight"]) == "lora"


def test_controlnet():
    assert _classify_safetensors_keys(["control_model.input_blocks.0.0.weight",
                                       "input_hint_block.0.weight"]) == "controlnet"


def test_checkpoint_full_stack():
    # unet + vae + text_encoder = 完整 checkpoint
    keys = [
        "model.diffusion_model.input_blocks.0.0.weight",
        "first_stage_model.encoder.conv_in.weight",
        "cond_stage_model.transformer.text_model.embeddings.weight",
    ]
    assert _classify_safetensors_keys(keys) == "checkpoint"


def test_unet_only():
    assert _classify_safetensors_keys(["model.diffusion_model.input_blocks.0.0.weight"]) == "unet"


def test_vae_only():
    assert _classify_safetensors_keys(["first_stage_model.encoder.conv_in.weight",
                                       "first_stage_model.decoder.conv_out.weight"]) == "vae"


# --- 新增类型 ---

def test_text_encoder_t5():
    # umt5_xxl / T5：shared.* + encoder.block.*
    keys = ["shared.weight", "encoder.block.0.layer.0.SelfAttention.q.weight",
            "encoder.final_layer_norm.weight"]
    assert _classify_safetensors_keys(keys) == "text_encoder"


def test_text_encoder_llm():
    # qwen_2.5_vl 等 LLM/VLM 编码器
    keys = ["model.embed_tokens.weight", "model.layers.0.self_attn.q_proj.weight",
            "lm_head.weight"]
    assert _classify_safetensors_keys(keys) == "text_encoder"


def test_clip_vision():
    keys = ["vision_model.embeddings.patch_embedding.weight",
            "vision_model.encoder.layers.0.self_attn.q_proj.weight"]
    assert _classify_safetensors_keys(keys) == "clip_vision"


def test_motion_module():
    keys = ["down_blocks.0.motion_modules.0.temporal_transformer.norm.weight"]
    assert _classify_safetensors_keys(keys) == "motion"


def test_text_encoder_not_misread_as_unknown():
    # 回归：之前 standalone TE 落到 ""→unknown
    assert _classify_safetensors_keys(["shared.weight", "encoder.block.0.layer.0.weight"]) != ""


# --- LoRA 家族细分（lora/lycoris/dora）---

def test_lora_subtype_plain():
    keys = ["lora_unet_down_blocks_0.lora_up.weight", "lora_unet_down_blocks_0.lora_down.weight"]
    assert classify_lora_subtype(keys) == "lora"


def test_lora_subtype_dora():
    # DoRA：含 dora_scale（优先于 lora 判定）
    keys = ["lora_unet_x.lora_up.weight", "lora_unet_x.dora_scale"]
    assert classify_lora_subtype(keys) == "dora"


def test_lora_subtype_lycoris_loha():
    # LoHa：hada_w1_a / hada_w2_b
    keys = ["lora_unet_x.hada_w1_a", "lora_unet_x.hada_w2_b"]
    assert classify_lora_subtype(keys) == "lycoris"


def test_lora_subtype_lycoris_lokr():
    # LoKr：lokr_w1 / lokr_w2
    keys = ["lora_unet_x.lokr_w1", "lora_unet_x.lokr_w2"]
    assert classify_lora_subtype(keys) == "lycoris"
