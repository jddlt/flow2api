import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.core.config import config
from src.services.flow_client import FlowClient
from src.services.generation_handler import GenerationHandler, MODEL_CONFIG
from src.services.token_manager import TokenManager


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
    async def test_upload_image_uses_project_scoped_flow_upload_endpoint(self):
        client = FlowClient(_ProxyManagerStub())
        client._make_request = AsyncMock(return_value={"media": {"name": "media-1"}})

        media_id = await client.upload_image(
            at="at-token",
            image_bytes=b"\x89PNG\r\n\x1a\nrest-of-png",
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            project_id="project-1",
        )

        self.assertEqual(media_id, "media-1")
        request = client._make_request.await_args.kwargs
        self.assertEqual(
            request["url"],
            f"{client.api_base_url}/flow/uploadImage",
        )
        payload = request["json_data"]
        self.assertEqual(payload["clientContext"]["tool"], "PINHOLE")
        self.assertEqual(payload["clientContext"]["projectId"], "project-1")
        self.assertEqual(payload["mimeType"], "image/png")
        self.assertTrue(payload["fileName"].endswith(".png"))
        self.assertEqual(payload["isUserUploaded"], True)
        self.assertEqual(payload["isHidden"], False)
        self.assertNotIn("imageInput", payload)

    async def test_generate_image_uses_structured_prompt_and_new_media_flags(self):
        client = FlowClient(_ProxyManagerStub())
        client._get_recaptcha_token = AsyncMock(return_value="captcha-token")
        client._make_request = AsyncMock(return_value={"media": [{"name": "media-1"}]})

        result = await client.generate_image(
            at="at-token",
            project_id="project-1",
            prompt="reference prompt",
            model_name="GEM_PIX_2",
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            image_inputs=[{"name": "media-input-1", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}],
        )

        payload = client._make_request.await_args.kwargs["json_data"]
        self.assertTrue(payload["useNewMedia"])
        self.assertIn("mediaGenerationContext", payload)
        self.assertEqual(
            payload["requests"][0]["structuredPrompt"]["parts"][0]["text"],
            "reference prompt",
        )
        self.assertNotIn("prompt", payload["requests"][0])
        self.assertEqual(result["_session_id"], payload["clientContext"]["sessionId"])

    async def test_generate_video_reference_images_uses_v2_model_config_payload(self):
        client = FlowClient(_ProxyManagerStub())
        client._get_recaptcha_token = AsyncMock(return_value="captcha-token")
        client._make_request = AsyncMock(return_value={"operations": [{"ok": True}]})

        await client.generate_video_reference_images(
            at="at-token",
            project_id="project-1",
            prompt="r2v prompt",
            model_key="veo_3_1_r2v_fast_landscape",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            reference_images=[{"imageUsageType": "IMAGE_USAGE_TYPE_ASSET", "mediaId": "media-1"}],
        )

        payload = client._make_request.await_args.kwargs["json_data"]
        self.assertTrue(payload["useV2ModelConfig"])
        self.assertIn("mediaGenerationContext", payload)
        self.assertEqual(
            payload["requests"][0]["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "r2v prompt",
        )
        self.assertNotIn("prompt", payload["requests"][0]["textInput"])

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


class TokenManagerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_credits_updates_user_paygate_tier(self):
        token = SimpleNamespace(id=1, at="at-token")
        db = SimpleNamespace(
            get_token=AsyncMock(side_effect=[token, token]),
            update_token=AsyncMock(),
        )
        flow_client = SimpleNamespace(
            get_credits=AsyncMock(
                return_value={
                    "credits": 123,
                    "userPaygateTier": "PAYGATE_TIER_ONE",
                }
            )
        )
        manager = TokenManager(db, flow_client)
        manager.is_at_valid = AsyncMock(return_value=True)

        credits = await manager.refresh_credits(1)

        self.assertEqual(credits, 123)
        db.update_token.assert_awaited_once_with(
            1,
            credits=123,
            user_paygate_tier="PAYGATE_TIER_ONE",
        )

    async def test_refresh_at_updates_user_paygate_tier_from_credits_response(self):
        token = SimpleNamespace(id=1, st="st-token")
        db = SimpleNamespace(
            get_token=AsyncMock(return_value=token),
            update_token=AsyncMock(),
        )
        flow_client = SimpleNamespace(
            st_to_at=AsyncMock(
                return_value={
                    "access_token": "new-at",
                    "expires": "2026-04-18T10:00:00.000Z",
                }
            ),
            get_credits=AsyncMock(
                return_value={
                    "credits": 456,
                    "userPaygateTier": "PAYGATE_TIER_ONE",
                }
            ),
        )
        manager = TokenManager(db, flow_client)

        success = await manager._refresh_at(1)

        self.assertTrue(success)
        self.assertEqual(db.update_token.await_count, 2)
        self.assertEqual(db.update_token.await_args_list[1].kwargs["credits"], 456)
        self.assertEqual(
            db.update_token.await_args_list[1].kwargs["user_paygate_tier"],
            "PAYGATE_TIER_ONE",
        )


class GenerationHandlerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_reference_upload_passes_project_id(self):
        flow_client = SimpleNamespace(
            upload_image=AsyncMock(return_value="uploaded-media-1"),
            generate_image=AsyncMock(
                return_value={
                    "_session_id": "session-1",
                    "media": [
                        {
                            "name": "generated-media-1",
                            "image": {
                                "generatedImage": {
                                    "fifeUrl": "https://example.com/image.jpg"
                                }
                            },
                        }
                    ]
                }
            ),
        )
        handler = GenerationHandler(
            flow_client=flow_client,
            token_manager=None,
            load_balancer=None,
            db=None,
            concurrency_manager=None,
            proxy_manager=_ProxyManagerStub(),
        )

        original_cache_enabled = config.cache_enabled
        config.set_cache_enabled(False)
        try:
            token = SimpleNamespace(id=1, at="at-token", user_paygate_tier="PAYGATE_TIER_ONE")
            model_config = MODEL_CONFIG["gemini-3.0-pro-image-landscape"]

            chunks = [
                chunk
                async for chunk in handler._handle_image_generation(
                    token=token,
                    project_id="project-1",
                    model_config=model_config,
                    prompt="prompt",
                    images=[b"image-bytes-1"],
                    stream=False,
                )
            ]
        finally:
            config.set_cache_enabled(original_cache_enabled)

        self.assertTrue(chunks)
        self.assertEqual(
            flow_client.upload_image.await_args.kwargs["project_id"],
            "project-1",
        )

    async def test_video_frame_upload_passes_project_id(self):
        flow_client = SimpleNamespace(
            upload_image=AsyncMock(return_value="frame-media-1"),
            generate_video_start_image=AsyncMock(return_value={"operations": []}),
        )
        handler = GenerationHandler(
            flow_client=flow_client,
            token_manager=None,
            load_balancer=None,
            db=SimpleNamespace(create_task=AsyncMock()),
            concurrency_manager=None,
            proxy_manager=_ProxyManagerStub(),
        )
        token = SimpleNamespace(id=1, at="at-token", user_paygate_tier="PAYGATE_TIER_ONE")
        model_config = MODEL_CONFIG["veo_3_1_i2v_s_fast_fl_landscape"]

        chunks = [
            chunk
            async for chunk in handler._handle_video_generation(
                token=token,
                project_id="project-1",
                model_config=model_config,
                prompt="prompt",
                images=[b"frame-1"],
                stream=False,
            )
        ]

        self.assertTrue(chunks)
        self.assertEqual(
            flow_client.upload_image.await_args.kwargs["project_id"],
            "project-1",
        )

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
