import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BandageTwinView } from "./components/BandageTwinView";
import "./index.css";

// Standalone, auth-free entry for the self-contained bandage EIS twin (open /bandage-twin.html).
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <div className="mx-auto max-w-[1600px] p-4">
      <BandageTwinView />
    </div>
  </StrictMode>,
);
