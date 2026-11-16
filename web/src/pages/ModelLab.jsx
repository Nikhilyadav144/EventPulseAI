import { useState } from "react";
import { api, download } from "../api.js";
import { go } from "../nav.js";
import { Columns, HBar, RocChart } from "../components/charts.jsx";
import { Badge, Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, f3, pct, toast, useApi } from "../components/ui.jsx";

function Details({ id, onActivated }) {
  const { data, error, loading, reload } = useApi(`/api/models/${id}`);
  const imp = useApi("/api/analytics?dimension=Department");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const v = data.version, m = data.metadata, cm = data.confusion_matrix, cv = data.cv || {};
  const auc = cv.roc_auc?.mean;
  const activate = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/models/${id}/activate`, { method: "POST" });
      toast(`${v.name} v${v.version} is now the active model`);
      reload();
      onActivated();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
    const metrics = Object.entries(cv);
  return (
    <div className="stack">
      <Card eyebrow={`${v.algorithm} · trained ${v.trained_utc?.slice(0, 10)} · scikit-learn ${v.sklearn_version}`} title={`${v.name} v${v.version}`}
        actions={v.is_active ? <Badge tone="good">Active</Badge> : <Button busy={busy} disabled={data.problems.length > 0} onClick={activate}>Make active</Button>}>
        <p className="muted">{m.description}</p>
        <dl className="info">
          <dt>Hyperparameters</dt><dd><code>{JSON.stringify(m.best_params || {})}</code></dd>
          <dt>Decision threshold</dt><dd>{v.threshold} — {m.threshold_rule}</dd>
          <dt>Training data</dt><dd>{m.training_data?.label} · {m.training_data?.rows_train} of {m.training_data?.rows_total} rows used for training</dd>
          <dt>Artifact</dt><dd><code className="small">{v.artifact_path}</code> · sha256 {v.artifact_sha256?.slice(0, 12)}…</dd>
        </dl>
        {data.problems.map((p) => <Notice key={p} kind="error">{p}</Notice>)}
        <Notice kind="info">{m.target_notice}</Notice>
        <ErrorState error={err} />
      </Card>
      <Grid cols={2}>
        <Card title="ROC curve (holdout)" subtitle={data.holdout ? `${data.holdout.n} held-out registrations` : ""}>
          {data.roc_curve?.fpr?.length ? <RocChart points={data.roc_curve} auc={data.holdout?.roc_auc} /> : <Notice kind="note">No holdout curve stored.</Notice>}
        </Card>
        <Card title="Confusion matrix (holdout)" subtitle={`At threshold ${v.threshold}. Rows: actual; columns: predicted.`}>
          {cm ? (
            <table className="cm">
              <thead><tr><th /><th>Pred. no-show</th><th>Pred. attend</th></tr></thead>
              <tbody>
                <tr><th>Actual no-show</th><td className="ok">{cm[0][0]}<small>TN</small></td><td className="bad">{cm[0][1]}<small>FP</small></td></tr>
                <tr><th>Actual attend</th><td className="bad">{cm[1][0]}<small>FN</small></td><td className="ok">{cm[1][1]}<small>TP</small></td></tr>
              </tbody>
            </table>
          ) : <Notice kind="note">Not available.</Notice>}
          {data.holdout && (
            <Grid cols={3} className="mt">
              <Kpi label="Accuracy" value={pct(data.holdout.accuracy, 1)} />
              <Kpi label="Balanced acc." value={pct(data.holdout.balanced_accuracy, 1)} tone="blue" />
              <Kpi label="ROC-AUC" value={f3(data.holdout.roc_auc)} tone="good" />
            </Grid>
          )}
        </Card>
      </Grid>
      {metrics.length > 0 && (
        <Card title="Cross-validation" subtitle={`${cv.roc_auc?.folds?.length || 0}-fold stratified CV on the training split — the primary estimate (ROC-AUC ${f3(auc)} ± ${f3(cv.roc_auc?.std)}).`}>
          <Columns categories={metrics.map(([k]) => k.replace(/_/g, " "))} series={[{ name: "Mean across folds", values: metrics.map(([, x]) => x.mean) }]} yMax={1} height={220} as={(x) => x.toFixed(3)} />
          <DataTable search={false} rows={metrics.map(([k, x]) => ({ metric: k, mean: x.mean, std: x.std, folds: x.folds.map((f) => f.toFixed(3)).join(" · ") }))} />
        </Card>
      )}
      {v.is_active && imp.data?.importance?.length > 0 && (
        <Card title="Permutation importance (active model)" subtitle="Drop in ROC-AUC when a feature is shuffled. Association with the SIMULATED target, not causation.">
          <HBar data={imp.data.importance.slice(0, 12)} label="feature" value="mean" as={(x) => x.toFixed(3)} />
        </Card>
      )}
    </div>
  );
}

export default function ModelLab({ arg }) {
  const { data, error, loading, reload } = useApi("/api/models");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const vs = data.versions || [];
  const sel = arg ? Number(arg) : vs.find((v) => v.active)?.model_version_id;
  return (
    <>
      <PageHead title="Model Laboratory" subtitle="Registered model versions with their stored evaluation. Choose which model the app uses for predictions.">
        <Button kind="ghost" onClick={() => download("/api/export/models?fmt=xlsx")}>Export comparison</Button>
      </PageHead>
      <Card title="Model comparison" subtitle="Click a model to inspect it. Unsupported models (missing artifact or incompatible library version) cannot be activated.">
        <DataTable rows={vs} rowKey="model_version_id" selected={sel} onRow={(r) => go("models", r.model_version_id)} search={false}
          columns={["model", "version", "active", "baseline", "cv_roc_auc", "cv_roc_auc_std", "holdout_roc_auc", "accuracy", "balanced_accuracy", "f1", "brier", "majority_baseline_accuracy", "threshold", "supported"]}
          render={{ active: (x) => (x ? <Badge tone="good">Active</Badge> : ""), baseline: (x) => (x ? <Badge>Baseline</Badge> : ""), supported: (x) => (x ? "Yes" : <Badge tone="critical">No</Badge>),
            accuracy: (x) => pct(x, 1), balanced_accuracy: (x) => pct(x, 1), majority_baseline_accuracy: (x) => pct(x, 1) }} />
      </Card>
      {sel && <Details key={sel} id={sel} onActivated={reload} />}
      <Card title="MLflow experiment runs" subtitle="Read from the local MLflow tracking database (end-term training pipeline). The run marked 'saved model' is the one served by the CSL236 API.">
        <DataTable rows={data.mlflow || []} maxCols={12} render={{ saved_model: (x) => (x ? <Badge tone="good">saved model</Badge> : "") }} empty="No MLflow runs found (mlflow.db not available)" />
      </Card>
    </>
  );
}
