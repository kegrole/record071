# errpolicy

纯 Python 标准库实现的、由规则文件驱动的请求错误分类与重试引擎。分类判据、
处置策略、退避参数全部写在 JSON 规则文件里，代码里不写死任何策略。

## 运行

    python -m errpolicy.cli --outcomes demo/outcomes.json --policy demo/policy.json --out out/report.json

跑完会在 stdout 打印一行摘要（请求数、成功数、失败数、人工数、分类分布），
并把完整报告写入 --out 指定的 JSON 文件（目录不存在会自动创建）。
python -m errpolicy 等价于 python -m errpolicy.cli。

## 测试

    cd tests && python -m unittest test_errpolicy -v
    # 或在仓库根目录：python -m unittest tests.test_errpolicy -v

## 错误分类

分类只看结构化信号：传输层错误类型、HTTP 状态码、以及 2xx 响应的结构化解析
校验。响应正文里出现 "404" 之类的数字不影响分类——状态码 200 且结构匹配就是
成功。每个请求按它遇到的首个错误归类；重试后最终成功的请求仍按首个错误计入
per_class（例如 429 重试成功仍算 rate_limited）。

| 类别 (class) | 含义 | 判据（reason） |
|---|---|---|
| transient | 网络瞬时错误 | 传输错误 timeout / connection_reset；状态码 502 / 503 / 504 |
| permanent | 永久错误 | 状态码 404 / 410；传输错误 dns_failure / cert_error |
| rate_limited | 限流 | 状态码 429 |
| blocked | 封禁 | 状态码 403 且响应体含验证码特征（captcha_markers） |
| parse_error | 解析失败 | 状态码 2xx 但正文不匹配 parse_pattern（结构变化、正则不匹配） |
| ok | 无错 | 状态码 2xx 且结构校验通过 |

reason 到 class 的映射由规则文件的 classification 表给出，上表是
demo/policy.json 与内置默认值的口径；改表即可改归类（例如把 http_404 映射到
transient 让它可重试）。未列在表里的 reason 按内置规则兜底：http_502/503/504
归 transient，其余未知 http_* 与未知传输错误归 permanent。

## 规则文件（policy.json）格式

    {
      "defaults": {"max_attempts": 3, "backoff": {"base": 1, "multiplier": 2}},
      "sleep_scale": 0,
      "parse_pattern": "<title>[^<]+</title>",
      "captcha_markers": ["captcha"],
      "classification": {"http_404": "permanent", "http_429": "rate_limited", "...": "..."},
      "actions": {
        "transient": {"action": "retry", "max_attempts": 3,
                      "backoff": {"base": 1, "multiplier": 2}, "on_exhaust": "skip"},
        "rate_limited": {"action": "retry", "respect_retry_after": true, "on_exhaust": "skip"},
        "permanent": {"action": "skip"},
        "blocked": {"action": "report_manual"},
        "parse_error": {"action": "skip"}
      }
    }

可配项：

- defaults.max_attempts：全局默认最大尝试次数，默认 3（含首次请求）。
- defaults.backoff：全局默认退避参数 base（基数秒）与 multiplier（倍数）。
- sleep_scale：实际 sleep 的缩放系数，只加速真实等待，报告里记录的等待秒数
  永远是未缩放的原值。命令行 --sleep-scale 可覆盖。
- parse_pattern：2xx 响应必须匹配的正则，不匹配则归 parse_error；留空则不校验。
- captcha_markers：403 响应体的验证码特征子串（大小写不敏感），命中归 blocked。
- classification：reason -> class 映射表，逐条覆盖内置默认。
- actions：每个 class 的处置规则：
  - action：retry（重试）/ switch_source（换源）/ skip（跳过，进失败清单）/
    report_manual（上报人工，进人工清单）。
  - max_attempts：该类覆盖全局默认的尝试次数上限。
  - backoff：该类覆盖全局默认的退避参数。
  - respect_retry_after：true 时优先按响应头 Retry-After 的秒数等待。
  - on_exhaust：retry 耗尽后的终态处置（skip / report_manual / switch_source）。

## 退避序列

第 k 次重试前（k 从 1 开始）的等待为 base * multiplier^(k-1)。默认 base=1、
multiplier=2，序列即 1、2、4、8 秒，严格递增（multiplier 必须大于 1，否则
抛错）。permanent 类永不重试；retry 类在 attempts 达到 max_attempts 后按
on_exhaust 落终态。

## Retry-After 的处理

class 规则里 respect_retry_after 为 true 时（默认配置用于 rate_limited），
429 的等待秒数直接取响应头 Retry-After 的整数值，不走固定退避序列；头缺失或
无法解析时才回退到退避序列。记录进报告的等待秒数是响应给出的原值，不随
sleep_scale 缩放。

## 请求结果文件（outcomes.json）格式

一个数组，每项 {"url": "...", "script": [...]}。script 是第 1、2、3… 次尝试
依次得到的结果；次数超出 script 长度时重复最后一项。每次尝试二选一：

- 传输层错误：{"error": "timeout" | "connection_reset" | "dns_failure" | "cert_error"}
- HTTP 响应：{"status": 200, "headers": {"Retry-After": "7"}, "body": "..."}

## 报告字段

- total：请求总数。
- per_class：六个类别各自的请求数（按首个错误归类，合计等于 total）。
- successes：成功集，每条 {url, attempts}。
- failures：失败清单，每条 {url, class, attempts, action, waits}；waits 是
  每次重试前实际等待的秒数（未缩放）。
- manual：人工清单，每条 {url, class, attempts, action}。
- details：每个请求的完整明细 {url, class, attempts, waits, action, result}，
  成功请求的等待序列也在这里查。
- counts：{success, failure, manual} 三个清单的条目数，三者相加等于 total。
