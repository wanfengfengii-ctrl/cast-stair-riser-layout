import { useEffect, useMemo, useRef, useState } from 'react'

const FIELDS = [
  { key: 'floor_height_mm', label: '层高', testid: 'floor-height', hint: '上下楼层结构面标高差' },
  { key: 'run_length_mm', label: '水平可用长度', testid: 'run-length', hint: '梯段水平投影可用长度' },
  { key: 'riser_min_mm', label: '踏步高度下限', testid: 'riser-min' },
  { key: 'riser_max_mm', label: '踏步高度上限', testid: 'riser-max' },
  { key: 'tread_min_mm', label: '踏面深度下限', testid: 'tread-min' },
  { key: 'tread_max_mm', label: '踏面深度上限', testid: 'tread-max' },
  { key: 'target_riser_mm', label: '目标踏步高度', testid: 'target-riser' },
]

const DEFAULTS = {
  floor_height_mm: '3000',
  run_length_mm: '4800',
  riser_min_mm: '150',
  riser_max_mm: '190',
  tread_min_mm: '250',
  tread_max_mm: '320',
  target_riser_mm: '175',
}

const INTEGER_RE = /^\d+$/
// 浏览器安全整数上限 2^53 - 1：超出后 JSON 数字无法精确表示，必须明确拒绝而非静默舍入
const MAX_SAFE_INTEGER = 9007199254740991

function validate(values) {
  const errors = {}
  for (const f of FIELDS) {
    const raw = values[f.key].trim()
    if (raw === '') {
      errors[f.key] = '必填'
    } else if (!INTEGER_RE.test(raw) || parseInt(raw, 10) <= 0) {
      errors[f.key] = '必须为正整数（毫米）'
    } else if (!Number.isSafeInteger(Number(raw))) {
      errors[f.key] = `超出可精确表示的整数上限（${MAX_SAFE_INTEGER}）`
    }
  }
  const num = (k) => parseInt(values[k], 10)
  if (!errors.riser_min_mm && !errors.riser_max_mm && num('riser_min_mm') > num('riser_max_mm')) {
    errors.riser_max_mm = '区间下限不得大于上限'
  }
  if (!errors.tread_min_mm && !errors.tread_max_mm && num('tread_min_mm') > num('tread_max_mm')) {
    errors.tread_max_mm = '区间下限不得大于上限'
  }
  return errors
}

/** 展示用格式化：整数原样，否则保留两位小数。 */
function fmt(x) {
  if (typeof x !== 'number' || Number.isInteger(x)) return String(x)
  return x.toFixed(2).replace(/0$/, '').replace(/\.$/, '')
}

export default function App() {
  const [values, setValues] = useState(DEFAULTS)
  const [result, setResult] = useState(null)
  const [apiError, setApiError] = useState(null)
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)

  const errors = useMemo(() => validate(values), [values])
  const valid = Object.keys(errors).length === 0

  useEffect(() => {
    if (!valid) {
      // 字段非法：立即清除旧结果
      setResult(null)
      setApiError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    const id = ++requestId.current
    const timer = setTimeout(async () => {
      const payload = {}
      for (const f of FIELDS) payload[f.key] = parseInt(values[f.key], 10)
      try {
        const res = await fetch('/api/layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (id !== requestId.current) return // 已有更新的请求，丢弃过期响应
        if (!res.ok) {
          setResult(null)
          setApiError(`计算请求失败（HTTP ${res.status}）`)
        } else {
          setResult(await res.json())
          setApiError(null)
        }
      } catch {
        if (id !== requestId.current) return
        setResult(null)
        setApiError('无法连接计算服务')
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [values, valid])

  const onChange = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }))

  return (
    <div className="page">
      <header>
        <h1>混凝土楼梯支模放样工具</h1>
        <p className="subtitle">所有尺寸单位均为毫米（mm），须为正整数；踏步高度与踏面深度区间为闭区间，边界值有效。</p>
      </header>

      <section className="form-grid" data-testid="input-form">
        {FIELDS.map((f) => (
          <label key={f.key} className={`field${errors[f.key] ? ' field-error' : ''}`}>
            <span className="field-label">{f.label}（mm）</span>
            <input
              data-testid={f.testid}
              value={values[f.key]}
              onChange={onChange(f.key)}
              inputMode="numeric"
              autoComplete="off"
            />
            {f.hint && !errors[f.key] && <span className="field-hint">{f.hint}</span>}
            {errors[f.key] && (
              <span className="field-error-text" data-testid={`${f.testid}-error`}>
                {errors[f.key]}
              </span>
            )}
          </label>
        ))}
      </section>

      {loading && <p className="loading" data-testid="loading">计算中…</p>}
      {apiError && <p className="api-error" data-testid="api-error">{apiError}</p>}

      {result && result.status === 'no_solution' && (
        <section className="conclusion no-solution" data-testid="no-solution">
          <h2>无法放样</h2>
          <p>
            在 2–40 级踏步范围内，没有任何踏步数能同时满足踏步高度区间
            [{values.riser_min_mm}, {values.riser_max_mm}]mm 与踏面深度区间
            [{values.tread_min_mm}, {values.tread_max_mm}]mm。请调整区间或现场尺寸后重新放样。
          </p>
        </section>
      )}

      {result && result.status === 'ok' && result.solution && (
        <Solution result={result} />
      )}
    </div>
  )
}

function Solution({ result }) {
  const sol = result.solution
  return (
    <>
      <section className="conclusion" data-testid="solution">
        <h2>
          放样结论：<span data-testid="step-count">{sol.steps}</span> 级踏步（{sol.treads} 个踏面）
        </h2>
        <div className="summary-grid">
          <div>
            <span className="summary-label">精确踏步高度</span>
            <strong>{fmt(sol.exact_riser_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">与目标 {sol.target_riser_mm}mm 偏差</span>
            <strong>{fmt(sol.deviation_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">精确踏面深度</span>
            <strong>{fmt(sol.exact_tread_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">踏面放样取值（四舍五入到 1mm）</span>
            <strong data-testid="tread-display">{sol.tread_display_mm} mm</strong>
          </div>
          <div>
            <span className="summary-label">逐级最大高差</span>
            <strong>{sol.max_riser_diff_mm} mm</strong>
          </div>
          <div>
            <span className="summary-label">高度合计校验</span>
            <strong>{sol.total_height_mm} mm</strong>
          </div>
        </div>
      </section>

      <section>
        <h3>逐级踏步高度（余数从第一级起各 +1mm）</h3>
        <div className="table-wrap">
          <table data-testid="riser-table">
            <thead>
              <tr>
                <th>级号</th>
                <th>踏步高度（mm）</th>
                <th>累计标高（mm）</th>
              </tr>
            </thead>
            <tbody>
              {sol.riser_sequence_mm.map((h, i) => (
                <tr key={i} data-testid={`riser-row-${i + 1}`}>
                  <td>{i + 1}</td>
                  <td>{h}</td>
                  <td>{sol.cumulative_height_mm[i]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3>候选踏步数（2–40）可行性结论</h3>
        <div className="table-wrap">
          <table data-testid="candidate-table">
            <thead>
              <tr>
                <th>踏步数</th>
                <th>踏面数</th>
                <th>精确踏步高度（mm）</th>
                <th>精确踏面深度（mm）</th>
                <th>与目标偏差（mm）</th>
                <th>结论 / 淘汰原因</th>
              </tr>
            </thead>
            <tbody>
              {result.candidates.map((c) => (
                <tr
                  key={c.steps}
                  data-testid={`candidate-row-${c.steps}`}
                  className={c.selected ? 'row-selected' : c.feasible ? 'row-feasible' : 'row-rejected'}
                >
                  <td>{c.steps}</td>
                  <td>{c.treads}</td>
                  <td>{fmt(c.exact_riser_mm)}</td>
                  <td>{fmt(c.exact_tread_mm)}</td>
                  <td>{fmt(c.deviation_mm)}</td>
                  <td>
                    {c.selected
                      ? <span data-testid="candidate-selected">✓ 选中</span>
                      : c.reasons.join('；')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
