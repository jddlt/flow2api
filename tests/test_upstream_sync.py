import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.core.config import config
from src.services.flow_client import FlowClient
from src.services.generation_handler import GenerationHandler, MODEL_CONFIG


class _ProxyManagerStub:
    async def get_proxy_url(self):
        return None


class _CaptchaResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _CaptchaSession:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/createTask"):
            return _CaptchaResponse({"taskId": "task-1"})
        return _CaptchaResponse(
            {
                "status": "ready",
                "solution": {"gRecaptchaResponse": "captcha-token"},
            }
        )


class FlowClientProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsample_image_includes_user_tier_and_reuses_session(self):
        client = FlowClient(_ProxyManagerStub())
        client._get_recaptcha_token = AsyncMock(return_value="captcha-token")
        client._make_request = AsyncMock(return_value={"encodedImage": "encoded"})

        result = await client.upsample_image(
            at="at-token",
            project_id="project-1",
            media_id="media-1",
            target_resolution="UPSAMPLE_IMAGE_RESOLUTION_2K",
            user_paygate_tier="PAYGATE_TIER_ONE",
            session_id="session-123",
        )

        self.assertEqual(result, "encoded")
        payload = client._make_request.await_args.kwargs["json_data"]
        self.assertEqual(payload["mediaId"], "media-1")
        self.assertEqual(payload["targetResolution"], "UPSAMPLE_IMAGE_RESOLUTION_2K")
        self.assertEqual(payload["clientContext"]["sessionId"], "session-123")
        self.assertEqual(
            payload["clientContext"]["userPaygateTier"], "PAYGATE_TIER_ONE"
        )

    async def test_generate_video_text_supports_v2_model_config(self):
        client = FlowClient(_ProxyManagerStub())
        client._get_recaptcha_token = AsyncMock(return_value="captcha-token")
        client._make_request = AsyncMock(return_value={"operations": [{"ok": True}]})

        await client.generate_video_text(
            at="at-token",
            project_id="project-1",
            prompt="lite prompt",
            model_key="veo_3_1_t2v_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            use_v2_model_config=True,
        )

        payload = client._make_request.await_args.kwargs["json_data"]
        self.assertTrue(payload["useV2ModelConfig"])
        self.assertIn("mediaGenerationContext", payload)
        self.assertEqual(
            payload["requests"][0]["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "lite prompt",
        )

    async def test_captcha_json_requests_do_not_use_impersonation(self):
        client = FlowClient(_ProxyManagerStub())
        session = _CaptchaSession()

        original_method = config.captcha_method
        original_key = config.yescaptcha_api_key
        original_base_url = config.yescaptcha_base_url

        config.set_captcha_method("yescaptcha")
        config.set_yescaptcha_api_key("test-key")
        config.set_yescaptcha_base_url("https://captcha.example")

        try:
            with patch("src.services.flow_client.AsyncSession", return_value=session):
                token = await client._get_recaptcha_token("project-1", "IMAGE_GENERATION")
        finally:
            config.set_captcha_method(original_method)
            config.set_yescaptcha_api_key(original_key)
            config.set_yescaptcha_base_url(original_base_url)

        self.assertEqual(token, "captcha-token")
        self.assertGreaterEqual(len(session.calls), 2)
        for _, kwargs in session.calls:
            self.assertNotIn("impersonate", kwargs)


class GenerationHandlerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_upsample_uses_top_level_media_name(self):
        flow_client = SimpleNamespace(
            generate_image=AsyncMock(
                return_value={
                    "media": [
                        {
                            "name": "media-name-1",
                            "image": {
                                "generatedImage": {
                                    "fifeUrl": "https://example.com/image.jpg"
                                }
                            },
                        }
                    ]
                }
            ),
            upsample_image=AsyncMock(return_value="ZW5jb2RlZA=="),
        )
        handler = GenerationHandler(
            flow_client=flow_client,
            token_manager=None,
            load_balancer=None,
            db=None,
            concurrency_manager=None,
            proxy_manager=_ProxyManagerStub(),
        )
        handler.file_cache.save_base64 = Mock(return_value="cached.jpg")
        handler._get_base_url = Mock(return_value="http://localhost:8000")

        token = SimpleNamespace(id=1, at="at-token", user_paygate_tier="PAYGATE_TIER_TWO")
        model_config = MODEL_CONFIG["gemini-3.1-flash-image-landscape-upsample"]

        chunks = [
            chunk
            async for chunk in handler._handle_image_generation(
                token=token,
                project_id="project-1",
                model_config=model_config,
                prompt="prompt",
                images=None,
                stream=False,
            )
        ]

        self.assertTrue(chunks)
        self.assertEqual(
            flow_client.upsample_image.await_args.kwargs["media_id"], "media-name-1"
        )


class ModelConfigSyncTests(unittest.TestCase):
    def test_model_catalog_removes_veo2_and_adds_veo31_lite(self):
        self.assertNotIn("veo_2_1_fast_d_15_t2v_landscape", MODEL_CONFIG)
        self.assertNotIn("veo_2_0_t2v_landscape", MODEL_CONFIG)
        self.assertNotIn("veo_2_1_fast_d_15_i2v_landscape", MODEL_CONFIG)
        self.assertNotIn("veo_2_0_i2v_landscape", MODEL_CONFIG)

        self.assertIn("veo_3_1_t2v_lite_landscape", MODEL_CONFIG)
        self.assertIn("veo_3_1_t2v_lite_portrait", MODEL_CONFIG)
        self.assertIn("veo_3_1_i2v_lite_landscape", MODEL_CONFIG)
        self.assertIn("veo_3_1_i2v_lite_portrait", MODEL_CONFIG)
        self.assertIn("veo_3_1_interpolation_lite_landscape", MODEL_CONFIG)
        self.assertIn("veo_3_1_interpolation_lite_portrait", MODEL_CONFIG)
