import "./App.css";
import CheckPanel from "./components/CheckPanel";
import OrgBrowser from "./components/OrgBrowser";

function App() {
  return (
    <div className="app">
      <h1>Repository Access Service</h1>
      <div className="panels">
        <CheckPanel />
        <OrgBrowser />
      </div>
    </div>
  );
}

export default App;
