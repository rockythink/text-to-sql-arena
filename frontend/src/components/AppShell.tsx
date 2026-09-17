import { ArrowUpRight, Boxes, FlaskConical, Menu, MonitorPlay, Settings2 } from "lucide-react";
import type { PropsWithChildren, ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { Toaster } from "sonner";
import { useArenaStore } from "../store";
import { ModelIdentity } from "./ModelIdentity";

const nav = [
  { to: "/runs/new", label: "开赛与往期", icon: FlaskConical },
  { to: "/benchmarks", label: "赛题实验室", icon: Boxes },
  { to: "/models", label: "参赛模型", icon: Settings2 },
];

export function AppShell({ children }: PropsWithChildren) {
  const demo = useArenaStore((state) => state.demoMode);
  return <div className={`app-shell ${demo ? "is-demo" : ""}`}>
    <a className="skip-link" href="#main-content">跳到内容</a>
    <aside className="sidebar">
      <NavLink to="/runs/new" className="studio-brand" aria-label="拾穗数据工作室 · SQL 擂台首页"><span className="studio-logo">拾</span><b>拾穗数据工作室</b></NavLink>
      <div className="product-name"><b>SQL 擂台<span>本地工作台</span></b><p>同题较量，有据可查。</p></div>
      <nav aria-label="主导航">{nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}><Icon size={18}/><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-foot"><a href="https://arena.ss-data.cc/" target="_blank" rel="noreferrer">公开赛场<ArrowUpRight size={16}/></a><p>本地评测不会自动公开。<br/>历史结论，保留原始证据。</p></div>
    </aside>
    <main className="main-stage" id="main-content" tabIndex={-1}>{children}</main>
    <Toaster theme="dark" position="bottom-right" richColors/>
  </div>;
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: ReactNode }) {
  const demo = useArenaStore((state) => state.demoMode);
  const setDemo = useArenaStore((state) => state.setDemoMode);
  return <header className="page-header">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{description && <p className="page-description">{description}</p>}</div>
    <div className="header-actions">{actions}<button className={`icon-button ${demo ? "active" : ""}`} onClick={() => setDemo(!demo)} aria-pressed={demo} aria-label="切换录屏模式" title="隐藏导航，保留真实运行信息"><MonitorPlay size={18}/><span>{demo ? "退出录屏" : "录屏布局"}</span></button></div>
  </header>;
}

export function StatusPill({ status }: { status: string }) {
  const labels: Record<string, string> = { healthy: "可用", unknown: "待检查", checking: "检查中", unavailable: "不可用", incompatible: "不兼容", error: "异常", pending: "等待中", queued: "等待中", running: "进行中", cancelling: "正在取消", interrupted: "已中断", completed: "已完成", completed_with_errors: "完成 · 有失败", failed: "失败", cancelled: "已取消", published: "已锁定", draft: "草稿" };
  return <span className={`status-pill status-${status}`}><span aria-hidden="true"/>{labels[status] ?? status}</span>;
}

export function EmptyState({ icon = <Menu/>, title, body, action }: { icon?: ReactNode; title: string; body: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><h2>{title}</h2><p>{body}</p>{action}</div>;
}

export function Scoreboard({ models, suiteHash }: { models: Array<{ id: number; name: string; modelId?: string | null; adapterKind?: string | null; score: number | null; status: string }>; suiteHash: string }) {
  return <section className="scoreboard" aria-label="本场综合得分">
    <div className="scoreboard-title"><strong>本场记分牌</strong><small>综合得分 ≠ 正确率</small></div>
    <div className="score-list">{models.map((model, index) => <div className="score-chip" key={model.id}><span className="rank" aria-label="参赛编号">{String.fromCharCode(65 + index)}</span><div><b><ModelIdentity name={model.name} modelId={model.modelId} adapterKind={model.adapterKind} compact/></b><StatusPill status={model.status}/></div><em>{model.score == null ? "—" : model.score.toFixed(2)}</em></div>)}</div>
    <div className="hash-stamp"><small>题库指纹</small><code title={suiteHash}>{suiteHash ? suiteHash.slice(0, 12) : "等待生成"}</code></div>
  </section>;
}
