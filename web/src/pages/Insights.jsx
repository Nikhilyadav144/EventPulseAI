import { Badge, Card, ErrorState, Grid, Kpi, Loading, Notice, PageHead, num, useApi } from "../components/ui.jsx";

const LEVEL = { investigate: "critical", notable: "gold", info: "neutral" };

export default function Insights() {
  const { data, error, loading, reload } = useApi("/api/insights");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const groups = ["RECORDED", "SIMULATED"].map((s) => [s, data.insights.filter((i) => i.source === s)]);
  return (
    <>
      <PageHead title="Insights" subtitle="Plain-language findings generated from the stored records, each labelled with its data source and sample size." />
      <Grid cols={3}>
        <Kpi label="Findings" value={data.insights.length} />
        <Kpi label="From recorded attendance" value={num(data.counts.RECORDED)} caption="outcomes entered in the app" tone="good" />
        <Kpi label="From simulated outcomes" value={num(data.counts.SIMULATED)} caption="imported dataset" tone="gold" />
      </Grid>
      {groups.map(([src, items]) => (
        <Card key={src} title={src === "RECORDED" ? "Recorded attendance" : "Simulated dataset outcomes"} subtitle={src === "SIMULATED" ? "These describe the simulation rules, not observed behaviour." : "Based on attendance recorded after real events."}>
          {items.length ? (
            <div className="insights">
              {items.map((i) => (
                <article key={i.title + i.detail} className={`insight lvl-${i.level}`}>
                  <header><Badge tone={LEVEL[i.level]}>{i.level}</Badge> <span className="muted small">{i.kind} · n = {num(i.n)}</span></header>
                  <h3>{i.title}</h3>
                  <p>{i.detail}</p>
                </article>
              ))}
            </div>
          ) : <Notice kind="note">{src === "RECORDED" ? "Not enough recorded attendance yet for findings. Record attendance for completed events." : "No findings."}</Notice>}
        </Card>
      ))}
      <Card title="Limitations">
        <ul className="tips">{data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </Card>
    </>
  );
}
