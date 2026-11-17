# OCI 镜像注册表的多平台制品闭包采集资料服务

项目接收 oci-registry 来源资料，现有目录只核对 tag, manifest, blob, platform 的基本身份和引用。`capture_data.io` 冻结字节和请求来源，`capture_data.domain` 核对本领域记录结构，`capture_data.journal` 封存来源原文和版本，`capture_data.projection` 显示本地引用缺口。现有判断只覆盖本地资料，尚未推断外部服务的连续性或全量完整性。

## OCI 闭包抓取

`capture_data.oci` 先冻结 tag 实际解析到的 digest，再沿 index→平台 manifest→config/layer blob 收集字节。所有对象以 digest 与长度核验后才进入 `capture_data.store` 内容寻址缓存；授权头只发往注册表本源，令牌端点与跨域重定向不携带授权；分段响应按 Content-Range 组装，临时片段在轮次开始即弃，不参与恢复；相同 blob 跨平台共享缓存，任一平台缺失即 `deliverable=false`，不被其他平台的成功掩盖；tag 在抓取中移动时 `observations` 同时保留旧目标与新观测，`deliverable` 仅针对冻结目标；中断后用同一缓存目录续抓，只复用已核验对象。抓取结果经 `emit_source_file` 冻结为来源文档，按既有账本与投影规则评估可发布性。

```sh
python -m capture_data.cli fetch --registry https://registry.example.test --repo team/app --tag stable \
  --collection sample-007 --cache run/cache --journal run/journal.sqlite3
```

Python 3.11 以上，无第三方依赖。样例均为合成，不访问真实站点。

```sh
python -m unittest discover -s tests -v
python -m compileall -q capture_data tests
python -m capture_data.cli inspect data/normal.json
```

Dockerfile 提供 Linux 运行环境；`docker build -t source-capture .` 构建，`docker run --rm source-capture python -m unittest discover -s tests -v` 运行测试。

协议样例可通过 python -m capture_data.cli inspect data/protocol-case.json 查看。该命令仍只说明现有接收能力，不证明远端连续性。
