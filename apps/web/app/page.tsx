export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl items-center px-6 py-16">
      <section aria-labelledby="page-title" className="space-y-5">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-sky-300">
          Phase 1 foundation
        </p>
        <h1
          id="page-title"
          className="text-4xl font-semibold tracking-tight sm:text-6xl"
        >
          Incident Ops
        </h1>
        <p className="max-w-2xl text-lg leading-8 text-slate-300">
          A human-first incident response control plane with a narrow, auditable
          agent action boundary. Product screens arrive after the backend
          contracts are complete.
        </p>
      </section>
    </main>
  );
}
