import { useEffect, useState } from "react";
import type { OperationDetail, OperationSummary } from "../../shared/contracts";
import { EmptyState, ErrorState, LoadingState } from "../components/States";

type DetailTab = "documentation" | "spec" | "raw";

export function CatalogScreen() {
  const [query, setQuery] = useState("");
  const [method, setMethod] = useState("");
  const [operations, setOperations] = useState<OperationSummary[]>([]);
  const [selected, setSelected] = useState<OperationDetail>();
  const [tab, setTab] = useState<DetailTab>("documentation");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<unknown>();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(undefined);
      void window.mneme.catalog
        .searchOperations({ query, method: method || undefined, limit: 40 })
        .then(setOperations)
        .catch(setError)
        .finally(() => setLoading(false));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, method]);

  const open = async (operation: OperationSummary) => {
    setDetailLoading(true);
    setError(undefined);
    try {
      setSelected(await window.mneme.catalog.getOperation(operation.id));
      setTab("documentation");
    } catch (nextError) {
      setError(nextError);
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <section>
      <header className="page-header compact">
        <div>
          <span className="eyebrow">Indexed knowledge</span>
          <h1>Catalog</h1>
          <p>Search operations, inspect documentation, and view minimal spec slices.</p>
        </div>
      </header>
      <div className="search-bar">
        <span>⌕</span>
        <input
          autoFocus
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search operations, e.g. create a refund"
          value={query}
        />
        <select onChange={(event) => setMethod(event.target.value)} value={method}>
          <option value="">All methods</option>
          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </div>
      {error !== undefined && <ErrorState error={error} />}
      <div className="catalog-layout">
        <div className="results-panel">
          <div className="panel-title">
            <strong>Operations</strong>
            <span>{operations.length} results</span>
          </div>
          {loading ? (
            <LoadingState label="Searching catalog" />
          ) : operations.length ? (
            <div className="operation-list">
              {operations.map((operation) => (
                <button
                  className={selected?.id === operation.id ? "operation-row active" : "operation-row"}
                  key={operation.id}
                  onClick={() => void open(operation)}
                >
                  <span className={`method ${operation.method.toLowerCase()}`}>
                    {operation.method}
                  </span>
                  <span className="operation-copy">
                    <strong>{operation.summary}</strong>
                    <code>{operation.path}</code>
                  </span>
                  <span className="chevron">›</span>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title={query ? "No matching operations" : "Your catalog is empty"}
              detail={
                query
                  ? "Try a broader phrase or remove the method filter."
                  : "Add an OpenAPI source to make operations available here."
              }
            />
          )}
        </div>
        <div className="detail-panel">
          {detailLoading ? (
            <LoadingState label="Loading operation" />
          ) : selected ? (
            <>
              <div className="detail-header">
                <div>
                  <span className={`method ${selected.method.toLowerCase()}`}>
                    {selected.method}
                  </span>
                  <h2>{selected.summary}</h2>
                  <code>{selected.path}</code>
                </div>
                <button className="icon-button" onClick={() => setSelected(undefined)}>
                  ×
                </button>
              </div>
              <div className="tabs">
                {(["documentation", "spec", "raw"] as DetailTab[]).map((item) => (
                  <button
                    className={tab === item ? "active" : ""}
                    key={item}
                    onClick={() => setTab(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="detail-content">
                {tab === "documentation" &&
                  (selected.documentation || selected.description ? (
                    <div className="prose">
                      {selected.documentation ?? selected.description}
                    </div>
                  ) : (
                    <EmptyState
                      title="No documentation"
                      detail="This operation does not include descriptive documentation."
                    />
                  ))}
                {tab === "spec" && (
                  <pre>{JSON.stringify(selected.specSlice, null, 2)}</pre>
                )}
                {tab === "raw" && <pre>{JSON.stringify(selected.raw, null, 2)}</pre>}
              </div>
            </>
          ) : (
            <EmptyState
              title="Select an operation"
              detail="Operation documentation and its minimal OpenAPI slice will appear here."
            />
          )}
        </div>
      </div>
    </section>
  );
}
