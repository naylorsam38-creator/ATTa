import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
function App() {
  const [n, setN] = useState(0);
  return (<main><h1>Vite React Dashboard</h1><p>Widgets online: 3</p>
    <button onClick={() => setN(n + 1)}>Refresh ({n})</button></main>);
}
createRoot(document.getElementById('root')).render(<App />);
