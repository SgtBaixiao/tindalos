/**
 * site/QaView.tsx —— 规则问答：聊天式对话，question → qa() → 回答 + 来源卡片 + 模式徽标。
 */

import { useRef, useState, type FormEvent } from 'react';
import { qa } from './api';
import type { QaSource } from './types';

type Message =
  | { role: 'user'; text: string }
  | { role: 'assistant'; text: string; sources: QaSource[]; mode: 'llm' | 'local' };

const PLACEHOLDER =
  '例：使用「智力」检定成功需要掷出多少？目标数为多少？';

export function QaView() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);

  const scrollBottom = () => {
    const el = chatRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput('');
    setError(null);
    setBusy(true);
    setMessages((prev) => [...prev, { role: 'user', text: question }]);
    try {
      const res = await qa(question);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: res.answer,
          sources: res.sources ?? [],
          mode: res.mode,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: `出错了：${err instanceof Error ? err.message : String(err)}`,
          sources: [],
          mode: 'local',
        },
      ]);
    } finally {
      setBusy(false);
      scrollBottom();
    }
  };

  return (
    <section className="sx-qa" data-testid="sx-qa">
      <div className="sx-qa__chat" ref={chatRef}>
        {messages.length === 0 && (
          <p className="sx-qa__hint sx-empty">
            向规则书 / 模组材料提问，例如：&ldquo;调查员进行幸运检定失败会怎样？&rdquo;
          </p>
        )}
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="sx-bubble sx-bubble--user">
              {m.text}
            </div>
          ) : (
            <div key={i} className="sx-bubble sx-bubble--assistant">
              {m.text}
              <div className="sx-qa__foot">
                <span
                  className={`sx-mode-badge sx-mode-badge--${m.mode}`}
                  data-testid="qa-mode"
                >
                  {m.mode === 'llm' ? 'LLM 回答' : '本地检索'}
                </span>
                {m.sources.length > 0 && (
                  <ul className="sx-sources">
                    {m.sources.map((s, j) => (
                      <li key={j} className="sx-source-card">
                        <span className="sx-source-card__text">{s.text}</span>
                        <span className="sx-source-card__meta">
                          {s.module_title ?? s.module_id ?? '规则书'}
                          {s.score != null ? ` · ${Math.round(s.score * 100)}%` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ),
        )}
        {busy && (
          <div className="sx-qa__typing" role="status">
            思考中…
          </div>
        )}
        {error && <p className="sx-error">{error}</p>}
      </div>
      <form className="sx-qa__composer" onSubmit={(e) => void onSubmit(e)}>
        <input
          className="sx-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={PLACEHOLDER}
          aria-label="问题"
        />
        <button type="submit" className="sx-btn sx-btn--ink" disabled={busy}>
          发送
        </button>
      </form>
    </section>
  );
}
