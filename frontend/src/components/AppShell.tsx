import { Activity, Boxes, FlaskConical, Menu, MonitorPlay, Settings2 } from "lucide-react";
import type { PropsWithChildren, ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { Toaster } from "sonner";
import { useArenaStore } from "../store";

const nav = [
  { to: "/runs/new", label: "新建对局", icon: FlaskConical },
  { to: "/benchmarks", label: "基准赛题", icon: Boxes },
  { to: "/models", label: "模型选手", icon: Settings2 },
];

export function AppShell({ children }: PropsWithChildren) {
  const demo = useArenaStore((state) => state.demoMode);
  return <div className={`app-shell ${demo ? "is-demo" : ""}`}>
    <aside className="sidebar">
      <div className="brand-mark"><span>SQL</span><b>擂台</b></div>
      <p className="brand-caption">TEXT-TO-SQL LAB</p>
      <nav>{nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-foot"><span className="pulse-dot"/><div><b>LOCAL STUDIO</b><small>127.0.0.1 · ONLINE</small></div></div>
    </aside>
    <main className="main-stage">{children}</main>
    <Toaster theme="dark" position="bottom-right" richColors/>
  </div>;
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: ReactNode }) {
  const demo = useArenaStore((state) => state.demoMode);
  const setDemo = useArenaStore((state) => state.setDemoMode);
  return <header className="page-header">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{description && <p className="page-description">{description}</p>}</div>
    <div className="header-actions">{actions}<button className={`icon-button ${demo ? "active" : ""}`} onClick={() => setDemo(!demo)} aria-label="切换演示模式" title="演示模式"><MonitorPlay size={18}/><span>{demo ? "退出演示" : "演示模式"}</span></button></div>
  </header>;
}

export function StatusPill({ status }: { status: string }) {
  const labels: Record<string, string> = { healthy: "可上场", unknown: "待检查", checking: "检查中", unavailable: "不可用", incompatible: "不兼容", error: "异常", queued: "候场", running: "作答中", completed: "已完成", completed_with_errors: "有失误", failed: "失败", cancelled: "已取消", published: "已发布", draft: "草稿" };
  return <span className={`status-pill status-${status}`}><span/>{labels[status] ?? status}</span>;
}

export function EmptyState({ icon = <Menu/>, title, body, action }: { icon?: ReactNode; title: string; body: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><h2>{title}</h2><p>{body}</p>{action}</div>;
}

export function Scoreboard({ models, suiteHash }: { models: Array<{ id: number; name: string; score: number | null; status: string }>; suiteHash: string }) {
  return <section className="scoreboard">
    <div className="scoreboard-title"><Activity size={18}/><div><small>LIVE SCORE</small><strong>实时战况</strong></div></div>
    <div className="score-list">{models.map((model, index) => <div className="score-chip" key={model.id}><span className="rank">{String(index + 1).padStart(2, "0")}</span><div><b>{model.name}</b><small>{model.status}</small></div><em>{model.score == null ? "—" : model.score.toFixed(2)}</em></div>)}</div>
    <div className="hash-stamp"><small>SUITE HASH</small><code>{suiteHash ? suiteHash.slice(0, 12) : "pending"}</code></div>
  </section>;
}
