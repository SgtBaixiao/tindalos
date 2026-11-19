import { Component, Suspense, lazy, type ReactNode } from 'react';
import { useHashRoute } from './router';
import { Home } from './Home';
import { NAVIGATION } from './navigation';
import { Icon } from '../ui/Icon';
import { useTheme } from '../ui/theme';

const Workbench = lazy(() => import('./WorkbenchView').then((m) => ({ default: m.WorkbenchView })));
const Library = lazy(() => import('./LibraryView').then((m) => ({ default: m.LibraryView })));
const Qa = lazy(() => import('./QaView').then((m) => ({ default: m.QaView })));
const History = lazy(() => import('./HistoryView').then((m) => ({ default: m.HistoryView })));
const Replay = lazy(() => import('./ReplayView').then((m) => ({ default: m.ReplayView })));
const Eval = lazy(() => import('./EvalView').then((m) => ({ default: m.EvalView })));
const EvalDetail = lazy(() =>
  import('./EvalDetailView').then((m) => ({ default: m.EvalDetailView })),
);
const Memories = lazy(() => import('./MemoryView').then((m) => ({ default: m.MemoryIndex })));
const Memory = lazy(() => import('./MemoryView').then((m) => ({ default: m.MemoryView })));

class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <div className="sx-route-error" role="alert">
          <h2>页面暂时无法打开</h2>
          <p>重新载入以取得当前版本。</p>
          <button className="sx-btn" onClick={() => window.location.reload()}>
            重新载入
          </button>
        </div>
      );
    return this.props.children;
  }
}
export function SiteApp() {
  const { segments, route, navigate } = useHashRoute();
  const [theme, toggleTheme] = useTheme();
  const detail = segments.length > 1;
  const viewRoute =
    route === 'history' && detail ? 'replay' : route === 'eval' && detail ? 'eval-detail' : route;
  const title =
    viewRoute === 'replay'
      ? '剧本重放'
      : viewRoute === 'eval-detail'
        ? '评测详情'
        : (NAVIGATION.find((n) => n.route === route)?.title ?? '档案首页');
  let view: ReactNode;
  switch (route) {
    case 'workbench':
      view = <Workbench />;
      break;
    case 'library':
      view = <Library />;
      break;
    case 'qa':
      view = <Qa />;
      break;
    case 'history':
      view = detail ? (
        <Replay campaignId={segments[1]} navigate={navigate} />
      ) : (
        <History navigate={navigate} />
      );
      break;
    case 'eval':
      view = detail ? (
        <EvalDetail runId={segments[1]} navigate={navigate} />
      ) : (
        <Eval navigate={navigate} />
      );
      break;
    case 'memories':
      view = detail ? (
        <Memory campaignId={segments[1]} navigate={navigate} />
      ) : (
        <Memories navigate={navigate} />
      );
      break;
    default:
      view = <Home navigate={navigate} />;
  }
  const links = NAVIGATION.map((n) => (
    <a
      key={n.route}
      href={`#/${n.route}`}
      aria-current={route === n.route ? 'page' : undefined}
      className="sx-sidebar__link"
    >
      <Icon name={n.icon} />
      <span>{n.title}</span>
    </a>
  ));
  return (
    <div className="sx-site" data-route={viewRoute} data-testid="sx-site">
      <a
        href="#main-content"
        className="sx-skip"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
      >
        跳到主要内容
      </a>
      <aside className="sx-sidebar">
        <button className="sx-sidebar__brand" onClick={() => navigate('#/')} aria-label="返回首页">
          <Icon name="compass" />
          <strong>Tindalos</strong>
          <small>THE KEEPER'S ARCHIVE</small>
        </button>
        <p className="sx-sidebar__section">YOUR FIELD NOTES</p>
        <nav aria-label="主导航">{links}</nav>
        <div className="sx-sidebar__foot">
          <span className="sx-sidebar__dot" />
          个人备团档案<p>每个选择，都有迹可循。</p>
        </div>
      </aside>
      <header className="sx-topbar">
        <button className="sx-topbar__brand" onClick={() => navigate('#/')} aria-label="返回首页">
          Tindalos
        </button>
        <div className="sx-topbar__crumb">
          <span className="sx-topbar__edition">T / FIELDWORK</span>
          <span className="sx-topbar__title" data-testid="route-title">
            {title}
          </span>
        </div>
        <div className="sx-topbar__actions">
          {route !== 'home' && (
            <button className="sx-topbar__back" onClick={() => navigate('#/')}>
              回首页
            </button>
          )}
          <button
            className="sx-theme-toggle"
            onClick={toggleTheme}
            aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'}
          >
            <Icon name={theme === 'light' ? 'moon' : 'sun'} />
          </button>
        </div>
      </header>
      <nav className="sx-mobile-nav" aria-label="栏目导航">
        {NAVIGATION.map((n) => (
          <a
            key={n.route}
            href={`#/${n.route}`}
            aria-current={route === n.route ? 'page' : undefined}
          >
            {n.short}
          </a>
        ))}
      </nav>
      <main className="sx-content" id="main-content" tabIndex={-1}>
        <RouteBoundary key={segments.join('/')}>
          <Suspense
            fallback={
              <div className="sx-route-loading" role="status">
                <span />
                正在打开{title}…
              </div>
            }
          >
            {view}
          </Suspense>
        </RouteBoundary>
      </main>
    </div>
  );
}
