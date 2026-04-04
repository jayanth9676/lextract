export default function Loading() {
  return (
    <main className="pageShell">
      <section className="heroCard">
        <div className="loadingBlock loadingHero" />
        <div className="loadingPanel">
          <div className="loadingBlock loadingLine" />
          <div className="loadingBlock loadingLine short" />
          <div className="loadingBlock loadingButton" />
        </div>
      </section>

      <section className="statusGrid">
        {Array.from({ length: 4 }).map((_, idx) => (
          <article className="metricCard" key={idx}>
            <div className="loadingBlock loadingLine short" />
            <div className="loadingBlock loadingLine" />
          </article>
        ))}
      </section>

      <section className="workspaceGrid">
        <article className="paneCard">
          <div className="loadingBlock loadingViewport" />
        </article>
        <article className="paneCard">
          <div className="loadingBlock loadingViewport" />
        </article>
      </section>
    </main>
  );
}
