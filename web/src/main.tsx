import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { DebugPage } from "./DebugPage";
import "./styles.css";

const Page = window.location.pathname.replace(/\/$/, "") === "/debug"
  ? DebugPage
  : App;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Page />
  </React.StrictMode>,
);
