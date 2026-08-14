import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { AuthProvider } from "./auth/AuthContext.jsx";
import { initAnalytics } from "./analytics.js";
import "./dashboard/dashboard.css";
import "./auth/auth.css";
import "./admin/admin.css";

initAnalytics();   // env-gated GA4 / Clarity / Search Console (no-op without IDs)

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
