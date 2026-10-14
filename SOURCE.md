# 来源与验证

v0.2：增加 0–30 秒提交延迟（界面默认 5 秒），倒计时，以及等待期间的停止和关闭题目取消逻辑。

上游：https://github.com/thuhollow2/yuketang （MIT，Copyright (c) 2025 thuhollow2）

`core.py` 的扫码登录、课程列表、开课检查、进入课堂、WebSocket 消息和答题请求，改编自上游 `yuketang.py` 的对应逻辑。上游源文件已于 2026-10-14 阅读核对，许可证随本包提供。

本版本增加桌面界面，改为仅在收到新题事件后调用 AI；限制单选、多选；严格校验选项；取消默认答案；限制目标课程；记录明确的服务端提交结果；跨启动避免重复处理。没有带入上游的考试、加入新班级、外部消息推送、随机时间字段等功能。

AI 请求采用 Chat Completions 兼容消息格式：
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

无需 OpenAI SDK。服务商、模型和 API Key 都由用户自行配置；不保证每个兼容服务的图片能力和响应格式完全一致。

验证范围：

- 本机 Windows / Python 3.12，运行依赖可导入。
- 雨课堂公开扫码登录通道可连接，并返回二维码信息；没有登录用户账号。
- 28 项单元与模拟集成测试通过，覆盖题型限制、答案校验、只提醒/建议模式、停止与关闭题目、防重复、失败回执、延迟倒计时与取消及课堂消息到提交的流程。
- 桌面演示：4 道内置题，其中 2 次模拟提交、2 次跳过，无网络请求。
- 此版本的发布验证使用模拟接口，未完成真实 AI 与课堂提交的端到端验证。

从源码运行：Python 3.12，安装 `requirements.txt` 后执行 `python app.py`。
开发测试命令与打包命令记录在 `BUILD.md`。
