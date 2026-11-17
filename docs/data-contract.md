# oci-registry 来源契约

来源清单中的资产长度与 SHA-256 用于核对本地原文字节。文档的 collection 与 protocol 必须匹配清单，现有记录类型包括 tag, manifest, blob, platform。每条记录以 kind、id、version 定位，refs 指向同集合的其他记录，payload 原样留存。来源版本冲突会中断接收；本地引用闭合只表示当前收到的引用能定位，不表示远端序列或授权资料已经完整。

## 已接收的协议资料
现有入口保存 tag、index digest 与平台 manifest 的描述符。样例未取得平台 manifest 正文，因而不知道 config 与 layer 列表；这些 blob 尚不能进入引用图，更不能视为已下载或摘要已核验。

同集合第二次来源接收保存在 data/conflict-case.json。冲突事务必须保持第一份收据和旧投影。 OCI 第二次 tag 解析改指新 digest，内容身份分别保留，未取得平台正文仍不得发布。

同集合第二次来源接收保存在 data/conflict-case.json。冲突事务必须保持第一份收据和旧投影。 OCI 第二次 tag 解析改指新 digest，内容身份分别保留，未取得平台正文仍不得发布。

## 闭包抓取与恢复

`fetch` 命令把 tag 冻结为 digest 后按 digest 取件：对象经 digest 与长度核验才入缓存，临时片段在轮次开始即弃，中断续抓只复用已核验对象。报告 `platforms` 逐平台给出 complete/failed，任一平台缺失即 `deliverable=false`，不被其他平台掩盖。tag 在抓取中移动时 `observations` 保留旧目标与新观测，`deliverable` 仅说明本轮冻结目标能否交付完整制品，新观测目标需另起一轮。授权头只发往注册表本源；跨域下载地址与令牌端点不携带授权。抓取产出的记录文档入账本后，同一 (kind, id, version) 结论变化仍按既有冲突规则拒绝并保留首份收据。
