import { memo, useState, type FormEvent } from 'react';
import { Icon } from '../../ui/Icon';
export type CatalogItem = { id: string; title?: string; filename?: string };
export type GenerationInput = { module_text: string; module_id?: string; llm: boolean };
export const SourceForm = memo(function SourceForm({
  modules,
  busy,
  canContinue,
  onGenerate,
  onContinue,
}: {
  modules: CatalogItem[];
  busy: boolean;
  canContinue: boolean;
  onGenerate: (input: GenerationInput) => void;
  onContinue: (llm: boolean) => void;
}) {
  const [text, setText] = useState('');
  const [moduleId, setModuleId] = useState('');
  const [llm, setLlm] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (text.trim() || moduleId)
      onGenerate({ module_text: text.trim(), module_id: moduleId || undefined, llm });
  };
  return (
    <form className="wb-source" onSubmit={submit}>
      <div className="wb-section-label">
        <span>01 / 准备依据</span>
        <Icon name="book" />
      </div>
      <label className="wb-field">
        <span>模组资料</span>
        <select
          aria-label="已上传模组"
          value={moduleId}
          onChange={(e) => setModuleId(e.target.value)}
          disabled={busy}
        >
          <option value="">使用输入文本</option>
          {modules.map((m) => (
            <option key={m.id} value={m.id}>
              {m.filename ?? m.title ?? m.id}
            </option>
          ))}
        </select>
      </label>
      <label className="wb-field">
        <span>正文与备团前提</span>
        <textarea
          aria-label="模组文本"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="贴入模组正文，或补充本次备团的前提…"
          disabled={busy}
          rows={7}
        />
      </label>
      <label className="wb-model">
        <input
          type="checkbox"
          checked={llm}
          onChange={(e) => setLlm(e.target.checked)}
          disabled={busy}
        />
        <span>使用模型生成</span>
      </label>
      <button className="sx-btn sx-btn--accent" disabled={busy || (!text.trim() && !moduleId)}>
        生成备团材料
        <Icon name="arrow" />
      </button>
      <button
        className="sx-btn sx-btn--ghost"
        type="button"
        disabled={busy || !canContinue}
        onClick={() => onContinue(llm)}
      >
        准备下一场
      </button>
      <p className="wb-hint">原始材料是依据。生成内容可检查、修改，再决定采用。</p>
    </form>
  );
});
