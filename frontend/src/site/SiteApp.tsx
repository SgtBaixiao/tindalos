/**
 * site/SiteApp.tsx —— 站点外壳 + hash 路由。
 *
 * 路由表：
 *  - #/                        首页（四栏目入口）
 *  - #/workbench               剧本工作台（内嵌 App）
 *  - #/library                 模组资料库
 *  - #/qa                      规则问答
 *  - #/history                 历史记录
 *  - #/history/<campaignId>    剧本重放
 *  - #/eval                    评测列表
 *  - #/eval/<runId>            评测详情（L1..L6 trace + 标注）
 *
 * 首次挂载显示 Loading（品牌词升起 → 坠落淡出），随后进入首页。
 * 不依赖 react-router：window.location.hash + hashchange。
 */

import { useEffect, useState } from 'react';
import { useHashRoute } from './router';
import { Loading } from './Loading';
import { Home } from './Home';
import { WorkbenchView } from './WorkbenchView';
import { LibraryView } from './LibraryView';
import { QaView } from './QaView';
import { HistoryView } from './HistoryView';
import { ReplayView } from './ReplayView';
import { EvalView } from './EvalView';
import { EvalDetailView } from './EvalDetailView';

const ROUTE_TITLES: Record<string, string> = {
  workbench: '剧本工作台',
  library: '模组资料库',
  qa: '规则问答',
  history: '历史记录',
  replay: '剧本重放',
  eval: '评测',
  'eval-detail': '评测详情',
};

export function SiteApp() {
  const [booted, setBooted] = useState(false);
  const { segments, route, navigate } = useHashRoute();

  // 站点皮肤主题：未设置时默认纸白，并兼容现有 data-theme 机制
  useEffect(() => {
    if (!document.documentElement.dataset.theme) {
      document.documentElement.dataset.theme = 'light';
    }
  }, []);

  const isReplay = route === 'history' && segments.length >= 2;
  const isEvalDetail = route === 'eval' && segments.length >= 2;
  const viewRoute = isReplay ? 'replay' : isEvalDetail ? 'eval-detail' : route;
  const title = ROUTE_TITLES[viewRoute];

  let view: React.ReactNode;
  if (route === 'workbench') {
    view = <WorkbenchView />;
  } else if (route === 'library') {
    view = <LibraryView />;
  } else if (route === 'qa') {
    view = <QaView />;
  } else if (route === 'history' && isReplay) {
    view = <ReplayView campaignId={segments[1]} navigate={navigate} />;
  } else if (route === 'history') {
    view = <HistoryView navigate={navigate} />;
  } else if (route === 'eval' && isEvalDetail) {
    view = <EvalDetailView runId={segments[1]} navigate={navigate} />;
  } else if (route === 'eval') {
    view = <EvalView navigate={navigate} />;
  } else {
    view = <Home navigate={navigate} />;
  }

  return (
    <div className="sx-site" data-route={viewRoute} data-testid="sx-site">
      {!booted && <Loading onDone={() => setBooted(true)} />}

      <header className="sx-topbar">
        <button
          type="button"
          className="sx-topbar__brand"
          onClick={() => navigate('#/')}
          aria-label="返回首页"
        >
          SgtXLonelyHeartsClub
        </button>
        <div className="sx-topbar__crumb">
          <span className="sx-topbar__title" data-testid="route-title">
            {title ?? ''}
          </span>
          {route !== 'home' && (
            <button
              type="button"
              className="sx-topbar__back"
              onClick={() => navigate('#/')}
            >
              回首页
            </button>
          )}
        </div>
      </header>

      <main className="sx-content">{view}</main>
    </div>
  );
}
