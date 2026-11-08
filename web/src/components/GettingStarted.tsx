import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  CheckCircle2,
  Circle,
  Copy,
  ExternalLink,
  Info,
  KeyRound,
  Layers3,
  Link2,
  RefreshCw,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { useQuery } from "@/lib/api";
import { Button } from "./ui/button";
import { LoadingState } from "./ui/loading";
import { ErrorMessage } from "./operations/Common";
import { ProxyLinksDialog } from "./operations/ProxyLinksDialog";
interface Readiness {
  accounts: number;
  enabled_accounts: number;
  total_ips: number;
  available_ips: number;
  bound_ips: number;
  quality_groups: number;
  verified_connection: boolean;
  proxy_host: string;
  socks_port: number;
  http_port: number;
  probe_enabled: boolean;
  expired_bound_ips: number;
}
const steps = [
  {
    id: "account",
    title: "添加上游账号",
    hint: "导入你购买的 IP",
    icon: KeyRound,
  },
  {
    id: "quality",
    title: "确认资源与质量",
    hint: "先能用，再按项目分组",
    icon: ShieldCheck,
  },
  {
    id: "connect",
    title: "复制并验证链接",
    hint: "完成第一次真实连接",
    icon: Link2,
  },
  {
    id: "switch",
    title: "固定使用或换 IP",
    hint: "看懂 worker 与 session",
    icon: RefreshCw,
  },
  {
    id: "troubleshoot",
    title: "追踪与异常处理",
    hint: "知道发生了什么",
    icon: Workflow,
  },
  {
    id: "safety",
    title: "凭据、额度与日志",
    hint: "长期运行前的检查",
    icon: Layers3,
  },
] as const;
const progressKey = "rapip:guide:read:v1";
function loadRead(): string[] {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem(progressKey) || "[]",
    );
    return Array.isArray(value)
      ? value.filter(
          (v): v is string =>
            typeof v === "string" && steps.some((s) => s.id === v),
        )
      : [];
  } catch {
    return [];
  }
}
function Jump({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Button asChild size="sm" variant="secondary">
      <a href={to}>
        {children}
        <ArrowRight size={13} />
      </a>
    </Button>
  );
}
function Note({ children }: { children: ReactNode }) {
  return (
    <div className="guide-note">
      <Info size={16} />
      <div>{children}</div>
    </div>
  );
}
export function GettingStarted({
  notify,
  onConnect,
}: {
  notify: (text: string) => void;
  onConnect: () => void;
}) {
  const initial = new URLSearchParams(location.hash.split("?")[1]).get("step");
  const [selected, setSelected] = useState(
    steps.findIndex((s) => s.id === initial) >= 0
      ? steps.findIndex((s) => s.id === initial)
      : 0,
  );
  const [read, setRead] = useState(loadRead);
  const [links, setLinks] = useState(false);
  const query = useQuery<Readiness>("/onboarding", true, 10000);
  const data = query.data;
  const step = steps[selected];
  function choose(index: number) {
    setSelected(index);
    history.replaceState(null, "", "#guide?step=" + steps[index].id);
  }
  function markRead() {
    const next = read.includes(step.id) ? read : [...read, step.id];
    setRead(next);
    try {
      localStorage.setItem(progressKey, JSON.stringify(next));
    } catch {
      /* Optional per-browser reading history. */
    }
    if (selected < steps.length - 1) choose(selected + 1);
    else
      notify("已读完引导，仍可随时从侧栏重开；实际接入状态以上方检测结果为准");
  }
  const statuses = data
    ? [
        data.accounts > 0,
        data.available_ips + data.bound_ips > 0,
        data.verified_connection,
      ]
    : [];
  return (
    <div className="getting-started">
      <ErrorMessage message={query.error} />
      {query.isLoading && (
        <LoadingState label="正在检查当前接入情况…" rows={2} />
      )}
      {data && (
        <div className="guide-readiness" aria-label="当前接入状态">
          <div>
            <KeyRound size={17} />
            <strong>{data.accounts}</strong>
            <span>上游账号 · {data.enabled_accounts} 个启用</span>
          </div>
          <div>
            <Layers3 size={17} />
            <strong>{data.total_ips}</strong>
            <span>受管 IP · {data.available_ips} 个待分配</span>
          </div>
          <div>
            <Link2 size={17} />
            <strong>{data.bound_ips}</strong>
            <span>已绑定或预留 IP</span>
          </div>
          <div className={data.verified_connection ? "is-ready" : "is-pending"}>
            <CheckCircle2 size={17} />
            <strong>{data.verified_connection ? "已验证" : "待验证"}</strong>
            <span>
              {data.verified_connection
                ? "保留记录中有成功代理连接"
                : "实际使用链接后自动确认"}
            </span>
          </div>
        </div>
      )}
      <div className="guide-layout">
        <nav className="surface guide-steps" aria-label="新手引导步骤">
          <div className="guide-steps-heading">
            <BookOpen size={16} />
            <b>接入路线</b>
            <span>
              {read.length} / {steps.length} 已读
            </span>
          </div>
          {steps.map((item, i) => (
            <button
              key={item.id}
              type="button"
              aria-current={selected === i ? "step" : undefined}
              className={selected === i ? "is-selected" : ""}
              onClick={() => choose(i)}
            >
              <span className="guide-step-number">
                {read.includes(item.id) ? (
                  <Check size={13} />
                ) : (
                  String(i + 1).padStart(2, "0")
                )}
              </span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.hint}</small>
              </span>
              {i < 3 && data && (
                <span
                  className={`guide-step-status ${statuses[i] ? "is-ready" : ""}`}
                  title={
                    statuses[i]
                      ? "检测到实际完成记录"
                      : "尚未检测到实际完成记录"
                  }
                >
                  {statuses[i] ? (
                    <CheckCircle2 size={13} />
                  ) : (
                    <Circle size={12} />
                  )}
                </span>
              )}
            </button>
          ))}
          <p>“已读”只保存在当前浏览器；右侧勾号根据真实资源与连接记录判断。</p>
        </nav>
        <section
          className="surface guide-content"
          aria-labelledby="guide-step-title"
        >
          <header>
            <span className="eyebrow">
              STEP {String(selected + 1).padStart(2, "0")} / 06
            </span>
            <h2 id="guide-step-title">
              <step.icon size={20} />
              {step.title}
            </h2>
          </header>
          {step.id === "account" && (
            <>
              <p>
                先把 Webshare 账号接入 RapIP，系统才能同步并管理你购买的
                IP。账号名称、备注方便识别，账号 ID 会自动生成。
              </p>
              <ol className="guide-instructions">
                <li>打开“上游账号”，点击“添加账号”。</li>
                <li>填写显示名称和 Webshare API Key，点击“验证并添加”。</li>
                <li>
                  等待同步完成，在卡片中确认 IP
                  数量、供应商更换额度和套餐剩余流量。
                </li>
              </ol>
              <Note>
                Webshare API Key 用于管理供应商资源；代理客户端使用的是 RapIP
                生成的代理链接。两者不是同一份凭据。
              </Note>
              <div className="guide-actions">
                <Jump to="#accounts?action=add">添加上游账号</Jump>
                <Jump to="#settings?tab=runtime">确认服务器与端口</Jump>
              </div>
            </>
          )}
          {step.id === "quality" && (
            <>
              <p>
                刚同步的 IP
                会检查可用性。“待分配”表示可以分配给新链接；“使用中”表示已有工位绑定，断开连接也会保留。
              </p>
              <div className="guide-pair">
                <article>
                  <h3>先用默认资源</h3>
                  <p>
                    无需先建质量分组。检查通过后就能复制链接，适合先跑通接入。
                  </p>
                </article>
                <article>
                  <h3>按项目创建质量分组</h3>
                  <p>
                    选择未绑定
                    IP，设置质量等级，再筛查准入。不同项目可以采用不同要求；不必马上开启自动替换。
                  </p>
                </article>
              </div>
              <Note>
                <strong>“质量待复检”不是 IP 到期。</strong>{" "}
                检测结果超过有效期或规则更新后需要复检。启用主动测活时，使用中
                IP 会自动刷新结果；复检不换
                IP、不消耗更换额度，失败也保留当前绑定。第三方质量报告仅作参考，与分组准入分开。
              </Note>
              {data && !data.probe_enabled && (
                <p className="guide-warning">
                  当前主动测活已关闭，计划复检暂停；可以手动复检，或到设置中开启。
                </p>
              )}
              <div className="guide-actions">
                <Jump to="#nodes">查看 IP 资源池</Jump>
                <Jump to="#groups">配置质量分组</Jump>
                <Jump to="#settings?tab=scheduler">查看主动测活开关</Jump>
              </div>
            </>
          )}
          {step.id === "connect" && (
            <>
              <p>
                根据需要选择一种复制方式，把完整链接粘贴到客户端。链接包含代理密码，请妥善保存。
              </p>
              <div className="guide-pair">
                <article>
                  <span className="guide-kind">普通待用链接</span>
                  <h3>首次连接时分配 IP</h3>
                  <p>
                    点击“复制代理链接”，选择分组。复制不占用
                    IP；未使用时再次复制可能仍是同一份，启用后再获取下一份。
                  </p>
                  <Button size="sm" onClick={() => setLinks(true)}>
                    <Copy size={14} />
                    复制普通待用链接
                  </Button>
                </article>
                <article>
                  <span className="guide-kind fixed">指定某个 IP</span>
                  <h3>复制时立即预留</h3>
                  <p>
                    到资源池点击某行 SOCKS5 / HTTP。待分配 IP
                    会绑定到工位，其他工位不会再分到它；使用中的则复制当前完整链接。
                  </p>
                  <Jump to="#nodes">挑选 IP 并复制</Jump>
                </article>
              </div>
              <ol className="guide-instructions">
                <li>
                  浏览器自动化优先尝试 HTTP 代理；使用 v2rayN 时可复制含别名的
                  SOCKS5 链接。
                </li>
                <li>
                  在客户端执行一次代理检测，或通过代理请求出口查询网站。只有成功使用链接才算完成验证。
                </li>
                <li>
                  回到“工位与会话”和“连接记录”，核对工位、session、实际出口和建立结果。
                </li>
              </ol>
              <Note>
                管理页面域名用于访问工作台。代理服务器地址和 SOCKS5／HTTP
                端口以复制出的链接为准。标准 Chromium 不支持 SOCKS5
                用户名密码认证；遇到兼容问题可改用 HTTP 入口。{" "}
                <a
                  href="https://github.com/chromium/chromium/blob/main/net/docs/proxy.md#socksv5-proxy-scheme"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Chromium 兼容说明
                </a>
              </Note>
              {data && (
                <div className="guide-address">
                  <span>代理入口</span>
                  <code>{data.proxy_host || "请先在运行配置中确认地址"}</code>
                  <span>
                    SOCKS5 {data.socks_port} · HTTP {data.http_port}
                  </span>
                </div>
              )}
              <div className="guide-actions">
                <Jump to="#bindings">查看绑定与出口</Jump>
                <Jump to="#connections">检查实际连接</Jump>
                <Button variant="secondary" size="sm" onClick={onConnect}>
                  AI 接入说明与完整配置 <ExternalLink size={13} />
                </Button>
              </div>
            </>
          )}
          {step.id === "switch" && (
            <>
              <div className="guide-route">
                <span>质量分组</span>
                <ArrowRight size={14} />
                <span>worker 工位</span>
                <ArrowRight size={14} />
                <span>session</span>
                <ArrowRight size={14} />
                <span>实际出口 IP</span>
              </div>
              <p>
                <strong>固定使用：</strong>保留同一个 worker 与
                session。断开连接不回收 IP，session
                不会因空闲过期。手动更换、已开启的定时轮换或故障转移仍可能改变出口。
              </p>
              <p>
                <strong>主动换 IP：</strong>同一工位只修改 session
                后缀，按分组的动态切换规则取得新出口。原位更换需要等待；超时后使用同一个新
                session 退避重试，避免不停生成新 session。
              </p>
              <div className="guide-pair">
                <article>
                  <h3>池内切换</h3>
                  <p>
                    先获得同组合格库存，再补货旧
                    IP。没有库存时可能失败并保留原绑定。
                  </p>
                </article>
                <article>
                  <h3>原位更换</h3>
                  <p>
                    可设置为缺库存时原位换号，或始终原位换号。供应商处理、质量检测完成后，新出口才交给客户端。
                  </p>
                </article>
              </div>
              <Note>
                复制一份配置不会创造额外
                IP。全部工位被占用时，新的工位可能没有出口。长期复用现有工位，按需更换
                session；每组的工位范围以工作台显示为准。
              </Note>
              <div className="guide-actions">
                <Jump to="#groups">查看分组切换与轮换规则</Jump>
                <Jump to="#bindings">管理现有工位</Jump>
              </div>
            </>
          )}
          {step.id === "troubleshoot" && (
            <>
              <p>
                先看“连接记录”的具体失败原因，再从 IP 行点击“IP
                轨迹”。搜索会定位到该 IP 最近一次出现的事件，并高亮对应 IP。
              </p>
              <div className="guide-table-wrap">
                <table className="guide-table">
                  <thead>
                    <tr>
                      <th>看到的提示</th>
                      <th>意味着什么</th>
                      <th>先做什么</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>质量待复检</td>
                      <td>质量结果过期或规则更新，不是 session 到期</td>
                      <td>等待自动复检，或点击“复检”；当前绑定保留</td>
                    </tr>
                    <tr>
                      <td>证据不足 / 等待复检</td>
                      <td>检测数据暂不可用，尚不能确认合格</td>
                      <td>查看详情原因，稍后复检；不要因此连续换号</td>
                    </tr>
                    <tr>
                      <td>补货失败</td>
                      <td>补货已停止，并非仍在更换</td>
                      <td>查看任务与预检原因，再按原规则手动重试</td>
                    </tr>
                    <tr>
                      <td>session 已被替代或释放</td>
                      <td>客户端还在使用旧 session</td>
                      <td>更新客户端完整链接，并检查旧进程是否还在请求</td>
                    </tr>
                    <tr>
                      <td>本组库存不足</td>
                      <td>没有符合规则的待分配 IP</td>
                      <td>检查分组、占用及补货；需要时选择原位切换规则</td>
                    </tr>
                    <tr>
                      <td>上游网络失败 / 并发连接已满</td>
                      <td>网络建连失败，或单工位 TCP 已达到上限</td>
                      <td>区分目标故障与出口故障，检查真实连接数后再处理</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {data && data.expired_bound_ips > 0 && (
                <p className="guide-warning">
                  当前有 {data.expired_bound_ips} 个绑定 IP
                  的质量结果待复检，原连接配置仍保留。
                </p>
              )}
              <div className="guide-actions">
                <Jump to="#connections">查看连接记录</Jump>
                <Jump to="#lifecycle">打开代理追踪</Jump>
                <Jump to="#tasks">查看任务原因</Jump>
              </div>
            </>
          )}
          {step.id === "safety" && (
            <>
              <div className="guide-table-wrap">
                <table className="guide-table">
                  <thead>
                    <tr>
                      <th>操作</th>
                      <th>是否可能消耗 Webshare 更换额度</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>质量复检、查看报告、复制链接</td>
                      <td>不消耗；行内复制可能预留 IP</td>
                    </tr>
                    <tr>
                      <td>释放绑定</td>
                      <td>不消耗；原 IP 返回待分配池，原 session 失效</td>
                    </tr>
                    <tr>
                      <td>更换 session / 轮换出口</td>
                      <td>可能消耗；旧出口补货或原位换号会调用供应商</td>
                    </tr>
                    <tr>
                      <td>手动更换 IP / 自动筛查后的换号</td>
                      <td>真实提交替换时消耗；预检失败不等于已换号</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ol className="guide-instructions">
                <li>
                  在“账号与安全”分别设置管理登录和代理入口凭据；修改管理登录需重新登录，修改代理凭据需更新客户端。
                </li>
                <li>
                  在分组中设置自动替换、定时轮换的每日及本次总额度。额度为 0
                  的定时轮换表示暂停，不表示无限制。
                </li>
                <li>
                  在“数据生命周期”确认连接日志、质量报告、代理追踪与统计的保留期限。默认按分钟存储趋势，避免无意义的高频增长。
                </li>
                <li>
                  不要把完整代理链接、API Key 或管理 Token
                  发到公开日志、截图或群聊中。AI 接入说明可从配置弹窗复制。
                </li>
              </ol>
              <div className="guide-actions">
                <Jump to="#settings?tab=security">修改凭据</Jump>
                <Jump to="#settings?tab=retention">检查数据保留</Jump>
                <Jump to="#accounts">查看供应商额度</Jump>
              </div>
            </>
          )}
          <footer className="guide-footer">
            <span>
              {read.includes(step.id)
                ? "本浏览器已读过此节"
                : "阅读进度不会代替实际连接验证"}
            </span>
            <Button onClick={markRead}>
              <Check size={14} />
              {selected === steps.length - 1
                ? "已了解，完成阅读"
                : "已了解，继续下一节"}
              <ArrowRight size={13} />
            </Button>
          </footer>
        </section>
      </div>
      {links && (
        <ProxyLinksDialog
          initialGroup="default"
          close={() => setLinks(false)}
          notify={notify}
        />
      )}
    </div>
  );
}
