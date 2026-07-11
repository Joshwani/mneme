import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { CatalogSource, CatalogSourceDetail, SourceKind } from "../../shared/contracts";
import { EmptyState, ErrorState, LoadingState } from "../components/States";

export function SourcesScreen() {
  const [sources, setSources] = useState<CatalogSource[]>([]);
  const [kind, setKind] = useState<SourceKind>("url");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reindexing, setReindexing] = useState<string>();
  const [deleting, setDeleting] = useState<string>();
  const [selected, setSelected] = useState<CatalogSourceDetail>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<unknown>();

  const load = useCallback(() => {
    setLoading(true);
    setError(undefined);
    void window.mneme.catalog
      .listSources()
      .then(setSources)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const chooseFile = async () => {
    const path = await window.mneme.catalog.chooseSpecFile();
    if (path) setValue(path);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await window.mneme.catalog.addSource({ kind, value });
      setValue("");
      load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  };

  const reindex = async (source: CatalogSource) => {
    setReindexing(source.id);
    setError(undefined);
    try {
      const updated = await window.mneme.catalog.reindexSource(source.id);
      setSources((current) =>
        current.map((item) => (item.id === source.id ? updated : item)),
      );
    } catch (nextError) {
      setError(nextError);
    } finally {
      setReindexing(undefined);
    }
  };

  const open = async (source: CatalogSource) => {
    setDetailLoading(true);
    setError(undefined);
    try {
      setSelected(await window.mneme.catalog.getSource(source.id));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setDetailLoading(false);
    }
  };

  const remove = async (source: CatalogSource) => {
    if (!window.confirm(`Delete “${source.name}” and its indexed operations?`)) return;
    setDeleting(source.id);
    setError(undefined);
    try {
      await window.mneme.catalog.removeSource(source.id);
      setSources((current) => current.filter((item) => item.id !== source.id));
      if (selected?.id === source.id) setSelected(undefined);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setDeleting(undefined);
    }
  };

  return (
    <section>
      <header className="page-header compact">
        <div>
          <span className="eyebrow">Catalog management</span>
          <h1>Sources</h1>
          <p>Add an OpenAPI source or refresh an existing index.</p>
        </div>
      </header>
      <form className="source-form" onSubmit={(event) => void submit(event)}>
        <div className="segmented">
          {(["url", "file", "domain"] as SourceKind[]).map((item) => (
            <button
              className={kind === item ? "active" : ""}
              key={item}
              onClick={() => {
                setKind(item);
                setValue("");
              }}
              type="button"
            >
              {item === "url" ? "Spec URL" : item === "file" ? "Local file" : "Discover domain"}
            </button>
          ))}
        </div>
        <div className="input-action">
          <input
            onChange={(event) => setValue(event.target.value)}
            placeholder={
              kind === "url"
                ? "https://api.example.com/openapi.json"
                : kind === "file"
                  ? "Choose a JSON or YAML specification"
                  : "api.example.com"
            }
            readOnly={kind === "file"}
            value={value}
          />
          {kind === "file" && (
            <button className="button secondary" onClick={() => void chooseFile()} type="button">
              Choose file
            </button>
          )}
          <button className="button primary" disabled={!value || saving} type="submit">
            {saving ? "Indexing…" : "Add source"}
          </button>
        </div>
      </form>
      {error !== undefined && <ErrorState error={error} retry={load} />}
      <div className="section-heading">
        <h2>Indexed sources</h2>
        <span>{sources.length} total</span>
      </div>
      {loading ? (
        <LoadingState label="Loading sources" />
      ) : sources.length ? (
        <div className="source-list">
          {sources.map((source) => (
            <article className="source-row" key={source.id}>
              <div className="source-symbol">{source.kind === "file" ? "◇" : "◎"}</div>
              <button className="source-copy source-link" onClick={() => void open(source)}>
                <strong>{source.name}</strong>
                <span>{source.location || source.kind}</span>
              </button>
              <div className="source-meta">
                <strong>{source.operationCount}</strong>
                <span>operations</span>
              </div>
              <div className="source-meta">
                <strong>{source.status ?? "Ready"}</strong>
                <span>{source.indexedAt ? new Date(source.indexedAt).toLocaleDateString() : "Local"}</span>
              </div>
              <button
                className="button secondary"
                disabled={reindexing === source.id || deleting === source.id}
                onClick={() => void reindex(source)}
              >
                {reindexing === source.id ? "Reindexing…" : "Reindex"}
              </button>
              <button
                className="button danger"
                disabled={deleting === source.id || reindexing === source.id}
                onClick={() => void remove(source)}
              >
                {deleting === source.id ? "Deleting…" : "Delete"}
              </button>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No sources yet"
          detail="Add a specification URL, local file, or domain to build your catalog."
        />
      )}
      {detailLoading && <LoadingState label="Loading specification" />}
      {selected && !detailLoading && (
        <article className="spec-detail">
          <div className="detail-header">
            <div>
              <span className="eyebrow">Specification details</span>
              <h2>{selected.name}</h2>
              <code>{selected.location}</code>
            </div>
            <button className="icon-button" onClick={() => setSelected(undefined)}>
              ×
            </button>
          </div>
          <div className="spec-documentation">
            <pre>{JSON.stringify(selected.documentation, null, 2)}</pre>
          </div>
        </article>
      )}
    </section>
  );
}
