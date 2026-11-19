import { NAVIGATION } from './navigation';
import { Icon } from '../ui/Icon';
import './styles/home.css';
export const COLUMNS = NAVIGATION.map((n) => ({
  route: n.route,
  mark: n.icon,
  title: n.title,
  desc: n.description,
}));

function ArchiveDiagram() {
  return (
    <svg className="sx-home__diagram" viewBox="0 0 460 450" fill="none" aria-hidden="true">
      <path d="M230 15v420M15 225h430" stroke="currentColor" opacity=".14" />
      <circle cx="230" cy="225" r="171" stroke="currentColor" opacity=".2" strokeDasharray="2 5" />
      <circle cx="230" cy="225" r="117" stroke="currentColor" opacity=".22" />
      <path d="m230 54 171 171-171 171L59 225Z" stroke="currentColor" opacity=".36" />
      <path d="m230 109 116 116-116 116-116-116Z" stroke="currentColor" opacity=".4" />
      <path d="m230 54 64 171-64 171-64-171Z M59 225h342" stroke="currentColor" opacity=".24" />
      <path d="m230 182 43 43-43 43-43-43Z" fill="var(--t-paper)" stroke="var(--t-orange)" />
      <circle cx="230" cy="225" r="5" fill="var(--t-orange)" />
      {[
        ['230', '54'],
        ['401', '225'],
        ['230', '396'],
        ['59', '225'],
      ].map(([cx, cy]) => (
        <circle key={cx + cy} cx={cx} cy={cy} r="4" fill="var(--t-paper)" stroke="currentColor" />
      ))}
      <text x="239" y="28" className="sx-diagram-label">
        N / SOURCE
      </text>
      <text x="17" y="245" className="sx-diagram-label">
        01
      </text>
      <text x="401" y="245" className="sx-diagram-label">
        03
      </text>
      <text x="239" y="431" className="sx-diagram-label">
        S / CONTINUITY
      </text>
    </svg>
  );
}
export function Home({ navigate }: { navigate: (to: string) => void }) {
  return (
    <section className="sx-home" data-testid="sx-home">
      <div className="sx-home__folio">
        <span>THE KEEPER'S ARCHIVE</span>
        <span>备团手记 / VOL. 01</span>
      </div>
      <div className="sx-home__hero">
        <div className="sx-home__copy">
          <p className="sx-home__eyebrow">
            <span />
            从一份模组，到下一场故事
          </p>
          <h1>
            未知有边界，
            <br />
            故事有来处。
          </h1>
          <p className="sx-home__intro">
            把散落的场景、人物与线索放回同一张桌面。
            <br />
            在这里准备、推敲，留下真正发生的事。
          </p>
          <button className="sx-btn sx-btn--accent" onClick={() => navigate('#/workbench')}>
            进入剧本工作台
            <Icon name="arrow" />
          </button>
          <p className="sx-home__caption">TINDALOS · TRPG 备团工作台</p>
        </div>
        <div className="sx-home__art">
          <ArchiveDiagram />
          <span className="sx-home__art-note">FIG. 01 — THE THREADS WE FOLLOW</span>
        </div>
      </div>
      <div className="sx-home__section">
        <span>你的调查桌面</span>
        <span>准备 / 查证 / 延续</span>
      </div>
      <div className="sx-home__grid">
        {NAVIGATION.map((n, i) => (
          <button className="sx-home__node" key={n.route} onClick={() => navigate(`#/${n.route}`)}>
            <span className="sx-home__node-index">0{i + 1}</span>
            <Icon name={n.icon} />
            <span className="sx-home__node-title">{n.title}</span>
            <span className="sx-home__node-desc">{n.description}</span>
            <span className="sx-home__node-arrow">↗</span>
          </button>
        ))}
      </div>
      <footer className="sx-home__footer">
        <span>为人类守秘人保留判断的位置。</span>
        <span>T / NOTES & EVIDENCE</span>
      </footer>
    </section>
  );
}
