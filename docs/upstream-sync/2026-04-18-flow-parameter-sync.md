# 2026-04-18 Upstream Flow 参数同步记录

## 背景

- 本次对齐目标：只同步会影响最终发往 Flow 上游请求参数/请求体形状的改动。
- 审核窗口：`2026-03-21` 之后到 `2026-04-18` 的 upstream 变更。
- 当前分支：`feature/refactory`
- 下次继续同步时，以上述日期为新的基线继续往后比。

## 本次已同步的 upstream commit

### 1. 直接来自本次审核窗口

| Commit | 日期 | 状态 | 说明 |
| --- | --- | --- | --- |
| `748e5eca2e12f86973dfdc704134a611d2a1bb9d` | 2026-04-17 | 已同步 | 移除 `veo 2.1 / 2.0` 模型入口 |
| `2029b5578eb7d968fc959d48d1c2f188e029903b` | 2026-04-02 | 已同步 | 新增 `veo 3.1 lite`，并带入 lite 模型需要的 v2 视频请求体 |
| `d37f98bdcec838e1f893b62c342c3f9daad17bde` | 2026-04-02 | 已同步 | captcha JSON 请求去掉 `impersonate` |

### 2. 虽早于 2026-03-21，但本次为了保持 Flow 请求参数一致而补同步

| Commit | 日期 | 状态 | 说明 |
| --- | --- | --- | --- |
| `f23cd7ddf6320138b2fbd356f061557375e9d590` | 2026-02-27 | 已同步 | 图片生成改为 `structuredPrompt + useNewMedia + mediaGenerationContext` |
| `e5ef2388491c3a6105556f5026c1df02fed6cff0` | 2026-02-28 | 已同步 | 图片上传切到项目级 `/flow/uploadImage`，修复参考图挂错上下文问题 |
| `d4bb1519d57173ae02c92988c38162ce8a422f4c` | 2026-03-06 | 已同步 | `R2V` 请求体改为 upstream 当前 v2 结构 |
| `99bedf863fe164b032424ec97d40e35b0a0a7829` | 2026-01-26 | 部分借鉴 | 借鉴了图片 MIME 检测和高清化相关历史实现思路；未整包同步 |

## 本次实际对齐的 Flow 请求

以下请求在当前代码中已按 upstream 当前形状对齐：

1. 图片上传：`/flow/uploadImage`
2. 图片生成：`/projects/{project_id}/flowMedia:batchGenerateImages`
3. 图片高清化：`/flow/upsampleImage`
4. 文生视频：`/video:batchAsyncGenerateVideoText`
5. 多图视频：`/video:batchAsyncGenerateVideoReferenceImages`
6. 单帧视频：`/video:batchAsyncGenerateVideoStartImage`
7. 首尾帧视频：`/video:batchAsyncGenerateVideoStartAndEndImage`
8. 视频放大：`/video:batchAsyncGenerateVideoUpsampleVideo`
9. 视频状态查询：`/video:batchCheckAsyncVideoGenerationStatus`
10. 余额查询：`/credits`

## 本次同步的关键注意事项

### 1. 图片生成协议已经不是旧版 `prompt`

- 当前必须发送：
  - `structuredPrompt.parts[0].text`
  - `mediaGenerationContext.batchId`
  - `useNewMedia: true`
- 如果退回旧的 `prompt` 字段，实际请求可能直接被 Flow 500 或 400。

### 2. 图片上传必须优先走项目级接口

- 参考图上传现在应优先使用 `/flow/uploadImage`
- `clientContext` 里要带：
  - `tool: "PINHOLE"`
  - `projectId`
- 如果图片上传不带 `projectId`，参考图可能会挂到错误项目上下文。

### 3. 图片高清化的 `mediaId` 来源要用生成结果顶层 `media[0].name`

- 不要只依赖旧字段 `generatedImage.mediaGenerationId`
- 当前实现是：
  - 优先 `media[0].name`
  - 旧字段只做兜底

### 4. 图片高清化必须带回生成阶段上下文

- `upsampleImage` 现在要带：
  - `sessionId`
  - `projectId`
  - `userPaygateTier`
- 返回值取 `encodedImage`，不是整包 response 原样下传。

### 5. `veo 3.1 lite` 和 `R2V` 不能再按旧视频请求体发

- lite / v2 类视频请求当前需要：
  - `textInput.structuredPrompt`
  - `mediaGenerationContext.batchId`
  - `useV2ModelConfig: true`
- `R2V` 当前也已经按 v2 结构发送。

## 本次本地增加的回归测试

- `test_upload_image_uses_project_scoped_flow_upload_endpoint`
- `test_generate_image_uses_structured_prompt_and_new_media_flags`
- `test_generate_video_reference_images_uses_v2_model_config_payload`
- `test_upsample_image_includes_user_tier_and_reuses_session`
- `test_captcha_json_requests_do_not_use_impersonation`
- `test_image_reference_upload_passes_project_id`
- `test_image_upsample_uses_top_level_media_name`
- `test_video_frame_upload_passes_project_id`
- `test_generate_video_text_supports_v2_model_config`
- `test_model_catalog_removes_veo2_and_adds_veo31_lite`

## 下次继续同步时的任务清单

### A. 先看哪些 upstream commit

优先复查以下还未整包同步的 commit：

| Commit | 日期 | 当前处理 | 下次是否优先看 |
| --- | --- | --- | --- |
| `298d294ffb3ef92ce1f5e01b881c457dcaf8af92` | 2026-04-17 | 未同步 | 是。它主要是并发/会话复用/发车控制层，不是请求参数层；如果线上仍有稳定性问题，需要重新评估 |
| `36f8cfc` | 2026-04-16 | 未同步 | 是。若浏览器打码或端口占满问题再次出现，需要同步 |
| `da72d58` | 2026-04-15 | 未同步 | 否。配置持久化类，除非后台配置有需求 |
| `53565ec` | 2026-04-11 | 未同步 | 按需。范围较大，不能盲目合 |

### B. 每次必须手工核对的 Flow 请求

同步时不要只看 commit message，必须逐项比对以下请求体：

1. `upload_image`
2. `generate_image`
3. `upsample_image`
4. `generate_video_text`
5. `generate_video_reference_images`
6. `generate_video_start_image`
7. `generate_video_start_end`
8. `upsample_video`
9. `check_video_status`

### C. 每次同步后的最小验证

先跑：

```bash
./.venv/bin/python -m unittest tests.test_upstream_sync -v
./.venv/bin/python -m compileall src
```

部署后至少做 4 个实流量冒烟：

1. 带参考图的 `gemini-3.0-pro-image-landscape`
2. 一次图片高清化
3. 一次 `veo_3_1_t2v_lite_*`
4. 一次 `veo_3_1_r2v_fast_*`

## 下次同步建议命令

### 1. 看新窗口内的关键提交

```bash
git fetch upstream
git log --since='2026-04-18 00:00' --oneline upstream/main -- src/services/flow_client.py src/services/generation_handler.py
```

### 2. 直接搜请求体关键词

```bash
git log --oneline -S'useNewMedia' upstream/main -- src/services/flow_client.py
git log --oneline -S'/flow/uploadImage' upstream/main -- src/services/flow_client.py
git log --oneline -S'useV2ModelConfig' upstream/main -- src/services/flow_client.py src/services/generation_handler.py
git log --oneline -S'userPaygateTier' upstream/main -- src/services/flow_client.py src/services/generation_handler.py
```

## 结论

- 到 `2026-04-18` 为止，本仓库当前生成链路里实际发给 Flow 的请求参数已经按本次审核范围对齐。
- 下次继续同步时，不要从“业务功能名”切入，要从“最终请求体是否一致”切入。
- 只要 upstream 改了请求体形状，即使 commit 早于本次审核窗口，也必须补同步。
