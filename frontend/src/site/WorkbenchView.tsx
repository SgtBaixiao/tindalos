/**
 * site/WorkbenchView.tsx —— 剧本工作台：内嵌既有 <App/>（脚本节点图，不做改动），
 * 上方提供模组文本输入 + 实时生成进度带（ProgressBand live + moduleText）。
 *
 * 说明：App 在挂载时读 URL query 载入 campaign；本视图点「生成」后新剧本
 * 不自动替换节点图（遵守「不修改 App.tsx」约束）。生成完成后可在
 * 「历史记录」中重放新剧本 —— 进度带 done 帧会写入全局 store 的 campaignId。
 */

import { useState, type FormEvent } from 'react';
import App from '../App';
import { ProgressBand } from '../components/ProgressBand';

const INTRO =
  '输入模组标题 / 全文，实时 SSE 生成剧本节点图；生成完成后可在「历史记录」中重放。';

export function WorkbenchView() {
  const [text, setText] = useState('');
  const [submitted, setSubmitted] = useState<string | null>(null);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    setSubmitted(trimmed);
  };

  return (
    <section className="sx-workbench" data-testid="sx-workbench">
      <div className="sx-workbench__bar">
        <p className="sx-workbench__intro">{INTRO}</p>
        <form className="sx-workbench__toolbar" onSubmit={onSubmit}>
          <input
            className="sx-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="例：雾港之夜 —— 侦探受委托调查港口失踪案…"
            aria-label="模组文本"
          />
          <button type="submit" className="sx-btn sx-btn--ink">
            生成
          </button>
        </form>
        {submitted !== null && (
          <div className="sx-workbench__band">
            <ProgressBand key={submitted} live moduleText={submitted} />
          </div>
        )}
      </div>
      <div className="sx-workbench__stage">
        <App />
      </div>
    </section>
  );
}
