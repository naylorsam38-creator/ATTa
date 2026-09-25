export const dynamic = 'force-dynamic';
export default function Page() {
  const notes = ['Ship v117 validation', 'Review Recovery Spine evidence'];
  return (<main><h1>Next Notes</h1><ul>{notes.map(n => <li key={n}>{n}</li>)}</ul><p>Rendered at {new Date().toISOString()}</p></main>);
}
