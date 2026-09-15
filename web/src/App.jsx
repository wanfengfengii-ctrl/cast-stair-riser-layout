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
const EMPTY_ROW = () => ({ step: '', height_mm: '' })

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

/** 控制点录入的本地校验：正整数、位于首末级之间、级号与标高严格递增且小于层高。 */
function validateControlRows(rows, steps, floorHeight) {
  const errors = {}
  const parsed = []
  rows.forEach((row, i) => {
    const er = {}
    const stepRaw = row.step.trim()
    const heightRaw = row.height_mm.trim()
    let step = null
    let height = null
    if (stepRaw === '') {
      er.step = '必填'
    } else if (!INTEGER_RE.test(stepRaw) || parseInt(stepRaw, 10) <= 0) {
      er.step = '级号须为正整数'
    } else {
      step = parseInt(stepRaw, 10)
      if (!(step >= 1 && step <= steps - 1)) er.step = `须位于 1–${steps - 1} 级之间（不含首末级）`
    }
    if (heightRaw === '') {
      er.height_mm = '必填'
    } else if (!INTEGER_RE.test(heightRaw) || parseInt(heightRaw, 10) <= 0) {
      er.height_mm = '累计标高须为正整数（毫米）'
    } else if (!Number.isSafeInteger(Number(heightRaw))) {
      er.height_mm = `超出可精确表示的整数上限（${MAX_SAFE_INTEGER}）`
    } else {
      height = parseInt(heightRaw, 10)
      if (!(height < floorHeight)) er.height_mm = `须小于层高 ${floorHeight}mm`
    }
    if (Object.keys(er).length) errors[i] = er
    parsed.push({ step, height })
  })
  // 严格递增：级号与标高都须大于上一合法行
  let prevStep = 0
  let prevHeight = 0
  parsed.forEach((p, i) => {
    if (p.step !== null) {
      if (p.step <= prevStep) {
        errors[i] = { ...errors[i], step: `级号须严格递增（上一控制点为 ${prevStep} 级）` }
      } else {
        prevStep = p.step
      }
    }
    if (p.height !== null) {
      if (p.height <= prevHeight) {
        errors[i] = { ...errors[i], height_mm: `累计标高须严格递增（上一控制点为 ${prevHeight}mm）` }
      } else {
        prevHeight = p.height
      }
    }
  })
  return errors
}

/** 展示用格式化：整数原样，否则保留两位小数。 */
function fmt(x) {
  if (typeof x !== 'number' || Number.isInteger(x)) return String(x)
  return x.toFixed(2).replace(/0$/, '').replace(/\.$/, '')
}

/** 从 422 响应中取出可定位字段的说明文本。 */
async function readErrorDetail(res) {
  try {
    const body = await res.json()
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => d.msg).filter(Boolean).join('；')
    }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // 响应体不是 JSON：退回通用提示
  }
  return ''
}

/** 从 422 详情中解析定位到具体控制点行/字段的反馈。 */
async function readControlErrors(res) {
  let detail = null
  try {
    detail = (await res.clone().json()).detail
  } catch {
    return { summary: '', rowErrors: {} }
  }
  const rowErrors = {}
  const messages = []
  if (Array.isArray(detail)) {
    for (const e of detail) {
      const loc = Array.isArray(e.loc) ? e.loc : []
      const idx = loc.indexOf('control_points')
      if (idx >= 0 && loc.length > idx + 2) {
        const row = Number(loc[idx + 1])
        const field = loc[idx + 2] === 'step' ? 'step' : 'height_mm'
        rowErrors[row] = { ...rowErrors[row], [field]: e.msg }
      }
      if (e.msg) messages.push(e.msg)
    }
  }
  return { summary: messages.join('；'), rowErrors }
}

export default function App() {
  const [values, setValues] = useState(DEFAULTS)
  const [result, setResult] = useState(null)
  const [apiError, setApiError] = useState(null)
  // 人工选用：{ steps, nonce }；null 表示自动推荐。nonce 允许对同一踏步数重新发起改选请求。
  const [selection, setSelection] = useState(null)
  const [switchError, setSwitchError] = useState(null)
  // 中间标高控制点：drafts 为录入行；controls 为已成功应用（随请求上送）的控制点
  const [controlRows, setControlRows] = useState([EMPTY_ROW()])
  const [controls, setControls] = useState(null)
  const [controlError, setControlError] = useState(null)
  const [controlRowErrors, setControlRowErrors] = useState({})
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)

  const errors = useMemo(() => validate(values), [values])
  const valid = Object.keys(errors).length === 0
  const clientControlErrors = useMemo(() => {
    if (!result || result.status !== 'ok' || !result.solution) return {}
    return validateControlRows(
      controlRows,
      result.solution.steps,
      parseInt(values.floor_height_mm, 10),
    )
  }, [controlRows, result, values.floor_height_mm])

  useEffect(() => {
    if (!valid) {
      // 字段非法：立即清除旧结果
      setResult(null)
      setApiError(null)
      setSwitchError(null)
      setControlError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    const id = ++requestId.current
    // 本次请求是否来自人工改选/控制点应用（决定失败时是否保留当前方案）
    const interactive = selection !== null || controls !== null
    const withControls = controls !== null
    const timer = setTimeout(async () => {
      const payload = {}
      for (const f of FIELDS) payload[f.key] = parseInt(values[f.key], 10)
      if (selection) payload.selected_steps = selection.steps
      if (withControls) {
        payload.control_points = controls.map((r) => ({
          step: parseInt(r.step, 10),
          height_mm: parseInt(r.height_mm, 10),
        }))
      }
      try {
        const res = await fetch('/api/layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (id !== requestId.current) return // 已有更新的请求，丢弃过期响应
        if (!res.ok) {
          if (withControls) {
            // 控制点应用失败：保留当前有效方案，在录入区原位给出定位到行的字段反馈
            const parsed = await readControlErrors(res)
            setControlRowErrors(parsed.rowErrors)
            const wanted = selection?.steps
            setControlError(
              `控制点未能应用（HTTP ${res.status}）${wanted ? `，当前仍显示 ${wanted} 级原方案` : '，当前仍显示原方案'}` +
                `${parsed.summary ? `：${parsed.summary}` : '。'}`,
            )
          } else if (interactive) {
            // 改选失败：保留当前有效方案，原位提示未能切换，避免现场误把失败当成功
            const detail = await readErrorDetail(res)
            const wanted = selection?.steps
            setSwitchError(
              `未能切换到 ${wanted} 级踏步方案（HTTP ${res.status}）${detail ? `：${detail}` : ''}，当前仍显示原方案。`,
            )
          } else {
            setResult(null)
            setApiError(`计算请求失败（HTTP ${res.status}）`)
          }
        } else {
          setResult(await res.json())
          setApiError(null)
          setSwitchError(null)
          setControlError(null)
          setControlRowErrors({})
        }
      } catch {
        if (id !== requestId.current) return
        if (withControls) {
          setControlError('无法连接计算服务，控制点未能应用；当前仍显示原方案。')
        } else if (interactive) {
          setSwitchError('无法连接计算服务，未能切换踏步方案；当前仍显示原方案。')
        } else {
          setResult(null)
          setApiError('无法连接计算服务')
        }
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [values, valid, selection, controls])

  const resetControls = () => {
    setControlRows([EMPTY_ROW()])
    setControls(null)
    setControlError(null)
    setControlRowErrors({})
  }

  const onChange = (key) => (e) => {
    setValues((v) => ({ ...v, [key]: e.target.value }))
    // 任一尺寸变化：清除人工选用与控制点（含未应用的录入与错误提示），恢复自动推荐
    if (selection !== null) setSelection(null)
    resetControls()
  }

  // 复用当前表单输入，仅携带 selected_steps 重新请求；改选踏步数时清空控制点
  const adopt = (steps) => {
    setSwitchError(null)
    setControlError(null)
    resetControls()
    setSelection({ steps, nonce: Date.now() })
  }

  const onRowChange = (i, field) => (e) => {
    setControlRows((rows) => rows.map((r, j) => (j === i ? { ...r, [field]: e.target.value } : r)))
  }
  const addRow = () => setControlRows((rows) => [...rows, EMPTY_ROW()])
  const removeRow = (i) => setControlRows((rows) => (rows.length <= 1 ? rows : rows.filter((_, j) => j !== i)))

  const applyControls = () => {
    const errs = validateControlRows(
      controlRows,
      result.solution.steps,
      parseInt(values.floor_height_mm, 10),
    )
    setControlRowErrors(errs)
    if (Object.keys(errs).length) {
      setControlError('控制点录入有误，请按各行提示修正后再应用。')
      return
    }
    setSwitchError(null)
    setControlError(null)
    // 新数组引用触发请求；内容相同也允许重复应用
    setControls(controlRows.map((r) => ({ ...r })))
  }

  const clearControls = () => {
    resetControls()
  }

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
      {switchError && <p className="api-error" data-testid="switch-error">{switchError}</p>}
      {controlError && <p className="api-error" data-testid="control-error">{controlError}</p>}

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
        <Solution
          result={result}
          switching={loading && selection !== null}
          onAdopt={adopt}
          controlRows={controlRows}
          onRowChange={onRowChange}
          onAddRow={addRow}
          onRemoveRow={removeRow}
          onApply={applyControls}
          onClear={clearControls}
          rowErrors={{ ...clientControlErrors, ...controlRowErrors }}
          busy={loading}
        />
      )}
    </div>
  )
}

function Solution({ result, switching, onAdopt, controlRows, onRowChange, onAddRow,
                    onRemoveRow, onApply, onClear, rowErrors, busy }) {
  const sol = result.solution
  const isManual = result.selection_source === 'manual'
  const controlled = sol.controlled === true
  const hits = Array.isArray(result.control_points) ? result.control_points : []
  return (
    <>
      <section className="conclusion" data-testid="solution">
        <h2>
          放样结论：<span data-testid="step-count">{sol.steps}</span> 级踏步（{sol.treads} 个踏面）
          {isManual ? (
            <span className="badge badge-manual" data-testid="selection-source">人工选用</span>
          ) : (
            <span className="badge badge-auto" data-testid="selection-source">自动推荐</span>
          )}
          {controlled && (
            <span className="badge badge-controlled" data-testid="controlled-badge">中间标高受控</span>
          )}
        </h2>
        {isManual && (
          <p className="selection-note" data-testid="selection-note">
            当前为现场人工选用方案；系统自动推荐为
            <strong data-testid="recommended-steps"> {result.recommended_steps} </strong>
            级。修改任一尺寸即恢复自动推荐。
          </p>
        )}
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
            <strong>{fmt(sol.max_riser_diff_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">高度合计校验</span>
            <strong>{sol.total_height_mm} mm</strong>
          </div>
        </div>

        <div className="control-panel" data-testid="control-panel">
          <h4>中间标高控制点（现场复测）</h4>
          <p className="control-hint">
            录入平台下口或转折级的“级号、累计标高”，应用后逐级表按控制点分段重新生成，
            控制点精确命中；级号须位于 1–{sol.steps - 1} 级之间且与标高一并严格递增。
            修改尺寸或改选踏步数将清空控制点。
          </p>
          <div className="control-rows">
            {controlRows.map((row, i) => {
              const er = rowErrors[i] || {}
              return (
                <div className="control-row" key={i} data-testid={`control-row-${i + 1}`}>
                  <span className="control-index">#{i + 1}</span>
                  <label className={`control-field${er.step ? ' field-error' : ''}`}>
                    <span>级号</span>
                    <input
                      data-testid={`control-step-${i + 1}`}
                      value={row.step}
                      onChange={onRowChange(i, 'step')}
                      inputMode="numeric"
                      autoComplete="off"
                    />
                    {er.step && (
                      <span className="field-error-text" data-testid={`control-step-${i + 1}-error`}>{er.step}</span>
                    )}
                  </label>
                  <label className={`control-field${er.height_mm ? ' field-error' : ''}`}>
                    <span>累计标高（mm）</span>
                    <input
                      data-testid={`control-height-${i + 1}`}
                      value={row.height_mm}
                      onChange={onRowChange(i, 'height_mm')}
                      inputMode="numeric"
                      autoComplete="off"
                    />
                    {er.height_mm && (
                      <span className="field-error-text" data-testid={`control-height-${i + 1}-error`}>{er.height_mm}</span>
                    )}
                  </label>
                  <button
                    type="button"
                    className="control-remove"
                    data-testid={`control-remove-${i + 1}`}
                    onClick={() => onRemoveRow(i)}
                    disabled={controlRows.length <= 1 || busy}
                  >
                    删除
                  </button>
                </div>
              )
            })}
          </div>
          <div className="control-actions">
            <button type="button" className="adopt-btn" data-testid="control-add" onClick={onAddRow} disabled={busy}>
              增加控制点
            </button>
            <button
              type="button"
              className="adopt-btn control-apply"
              data-testid="control-apply"
              onClick={onApply}
              disabled={busy}
            >
              应用控制点
            </button>
            {controlled && (
              <button
                type="button"
                className="control-clear"
                data-testid="control-clear"
                onClick={onClear}
                disabled={busy}
              >
                清除控制点
              </button>
            )}
          </div>
          {controlled && hits.length > 0 && (
            <div className="control-hits" data-testid="control-hits">
              <table>
                <thead>
                  <tr>
                    <th>控制点级号</th>
                    <th>录入累计标高（mm）</th>
                    <th>逐级表命中值（mm）</th>
                    <th>偏差（mm）</th>
                  </tr>
                </thead>
                <tbody>
                  {hits.map((cp, i) => (
                    <tr key={cp.step} data-testid={`control-hit-${i + 1}`}>
                      <td data-testid={`control-hit-${i + 1}-step`}>{cp.step}</td>
                      <td>{cp.height_mm}</td>
                      <td data-testid={`control-hit-${i + 1}-hit`}>{fmt(cp.hit_height_mm)}</td>
                      <td data-testid={`control-hit-${i + 1}-dev`}>{fmt(cp.deviation_mm)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section>
        <h3>
          {controlled
            ? '逐级踏步高度（按控制点分段：段内累计增量按半毫米向上取整）'
            : '逐级踏步高度（余数从第一级起各 +1mm）'}
        </h3>
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
                <tr
                  key={i}
                  data-testid={`riser-row-${i + 1}`}
                  className={hits.some((cp) => cp.step === i + 1) ? 'row-control-point' : undefined}
                >
                  <td>{i + 1}</td>
                  <td>{fmt(h)}</td>
                  <td>{fmt(sol.cumulative_height_mm[i])}</td>
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
                <th>操作</th>
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
                    {c.selected ? (
                      <span data-testid="candidate-selected">
                        ✓ 选中{isManual ? '（人工选用）' : '（自动推荐）'}
                      </span>
                    ) : c.recommended ? (
                      <>
                        <span className="badge badge-auto" data-testid="candidate-recommended">☆ 自动推荐</span>
                        <span className="cell-reason">{c.reasons.join('；')}</span>
                      </>
                    ) : (
                      c.reasons.join('；')
                    )}
                  </td>
                  <td>
                    {c.feasible && !c.selected && (
                      <button
                        type="button"
                        className="adopt-btn"
                        data-testid={`adopt-${c.steps}`}
                        disabled={switching || busy}
                        onClick={() => onAdopt(c.steps)}
                      >
                        采用此方案
                      </button>
                    )}
                    {c.feasible && c.selected && (
                      <button type="button" className="adopt-btn" disabled>
                        当前采用
                      </button>
                    )}
                    {!c.feasible && <span className="muted">—</span>}
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
