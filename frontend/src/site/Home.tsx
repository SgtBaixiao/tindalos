/**
 * site/Home.tsx —— 首页：四个栏目入口节点 + 标语。
 * 每个节点是一个「格子」：符号 + 标题 + 一句话描述，stagger 错峰揭示，点击跳 hash。
 */

import { sxVars } from './css';

type Column = {
  route: string;
  mark: string;
  title: string;
  desc: string;
};

export const COLUMNS: Column[] = [
  {
    route: 'workbench',
    mark: '◈',
    title: '剧本工作台',
    desc: '输入模组 → 实时 SSE 生成 → 节点图可编辑',
  },
  {
    route: 'library',
    mark: '▣',
    title: '模组资料库',
    desc: '上传规则书 PDF → 全文入库 + 多模态识图',
  },
  {
    route: 'qa',
    mark: '？',
    title: '规则问答',
    desc: '对规则书 / 模组材料提问，LLM 结合检索作答',
  },
  {
    route: 'history',
    mark: '◷',
    title: '历史记录',
    desc: '回看生成剧本与上传模组，随时重放',
  },
];

const SLOGAN = '随时可访问的 TRPG 备团工作台';

type HomeProps = {
  navigate: (to: string) => void;
};

export function Home({ navigate }: HomeProps) {
  return (
    <section className="sx-home" data-testid="sx-home">
      <div className="sx-home__head">
        <p className="sx-home__kicker">SgtXLonelyHeartsClub</p>
        <h1 className="sx-home__slogan">{SLOGAN}</h1>
      </div>
      <div className="sx-home__grid">
        {COLUMNS.map((col, i) => (
          <button
            key={col.route}
            type="button"
            className="sx-home__node"
            style={sxVars({ '--sx-stagger-i': i })}
            onClick={() => navigate(`#/${col.route}`)}
          >
            <span className="sx-home__node-mark" aria-hidden="true">
              {col.mark}
            </span>
            <span className="sx-home__node-title">{col.title}</span>
            <span className="sx-home__node-desc">{col.desc}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
