/**
 * Published reference data for the EIS twin: one open raw-spectrum deposit and a few
 * literature values of the same order of magnitude as the simulated sensor. Nothing here
 * feeds the model -- it is shown next to it (EisPanel "Published reference data") so the
 * simulated magnitudes can be judged against bench measurements.
 *
 * REFERENCE_SPECTRUM is derived from
 *   McLamore, E. "Electrochemical Sensor Data (Cavallaro et al)", Zenodo,
 *   doi:10.5281/zenodo.10719208, CC-BY-4.0, file "E coli_Calibration data_mixture.xlsx"
 * (laser-inscribed graphene + nano-platinum electrode, A8/LMCA2 aptamer coating, no redox
 * probe, modified Sonneveld's solution at 2.0 mS/cm, pH 5.7, 25 C; 3 replicate electrodes,
 * each a baseline and 6 E. coli doses). Each row is the mean over the 18 dose spectra
 * (6 doses x 3 replicates), log-interpolated onto the twin's 10-per-decade grid over the
 * range both cover (0.1 Hz .. 40 kHz):
 *   [freq Hz, Z' ohm, Z'' ohm (physical sign, negative = capacitive), min |Z|, max |Z|]
 *
 * Frequency axis: the deposit's dose blocks are labelled with a log grid (10 kHz .. 0.01 Hz),
 * but their values sit on the baseline block's grid (40 kHz, 30 k, 20 k, 10 k, 9 k, ...): matched
 * point-by-point, a dose spectrum is within 0.4-2 % of its own electrode's baseline, against
 * 9-10 % when matched by the printed frequency, and the step pattern of |Z| follows the
 * 1-2-...-9 spacing. The baseline block's frequencies are therefore used for every spectrum.
 * The traces are smooth to four decimals, so they read as exported circuit-model curves
 * rather than point-by-point analyser output.
 */
import type { EisSpectrum } from './types.ts';

export const REFERENCE_POINTS: readonly (readonly [number, number, number, number, number])[] = [
  [0.1, 1968.7, -7620.64, 7648.8, 8030.9],
  [0.125893, 1563.9, -6229.34, 6232.2, 6558.5],
  [0.158489, 1242.3, -5092.06, 5078.1, 5356.7],
  [0.199526, 986.9, -4162.42, 4138.0, 4377.2],
  [0.251189, 811.3, -3379.87, 3358.7, 3562.3],
  [0.316228, 671.9, -2742.05, 2725.5, 2897.7],
  [0.398107, 570.0, -2218.58, 2210.2, 2353.9],
  [0.501187, 495.8, -1791.04, 1793.0, 1911.3],
  [0.630957, 441.8, -1443.13, 1456.9, 1552.7],
  [0.794328, 401.6, -1161.07, 1187.5, 1264.8],
  [1.0, 372.6, -932.72, 973.1, 1035.5],
  [1.25893, 356.1, -747.44, 805.3, 855.2],
  [1.58489, 340.4, -598.97, 671.8, 713.1],
  [1.99526, 325.3, -479.99, 562.9, 601.6],
  [2.51189, 318.2, -383.75, 481.1, 518.6],
  [3.16228, 311.9, -306.72, 419.4, 456.3],
  [3.98107, 307.7, -244.92, 374.5, 411.3],
  [5.01187, 304.8, -195.44, 342.5, 383.8],
  [6.30957, 302.8, -155.85, 320.3, 365.1],
  [7.94328, 301.3, -124.22, 305.2, 352.5],
  [10.0, 300.3, -98.97, 295.0, 344.3],
  [12.5893, 299.8, -78.78, 288.5, 339.0],
  [15.8489, 299.3, -62.71, 284.1, 335.5],
  [19.9526, 298.7, -49.92, 281.1, 333.1],
  [25.1189, 298.5, -39.71, 279.3, 331.6],
  [31.6228, 298.3, -31.59, 278.1, 330.7],
  [39.8107, 298.2, -25.12, 277.3, 330.1],
  [50.1187, 298.1, -19.97, 276.9, 329.7],
  [63.0957, 298.1, -15.87, 276.5, 329.4],
  [79.4328, 298.0, -12.62, 276.3, 329.3],
  [100.0, 298.0, -10.03, 276.2, 329.2],
  [125.893, 298.0, -7.97, 276.1, 329.1],
  [158.489, 298.0, -6.33, 276.1, 329.1],
  [199.526, 298.0, -5.03, 276.0, 329.1],
  [251.189, 298.0, -4.0, 276.0, 329.0],
  [316.228, 298.0, -3.17, 276.0, 329.0],
  [398.107, 298.0, -2.52, 276.0, 329.0],
  [501.187, 298.0, -2.0, 276.0, 329.0],
  [630.957, 298.0, -1.59, 276.0, 329.0],
  [794.328, 298.0, -1.26, 276.0, 329.0],
  [1000.0, 298.0, -1.0, 276.0, 329.0],
  [1258.93, 298.0, -0.8, 276.0, 329.0],
  [1584.89, 298.0, -0.63, 276.0, 329.0],
  [1995.26, 298.0, -0.5, 276.0, 329.0],
  [2511.89, 298.0, -0.4, 276.0, 329.0],
  [3162.28, 298.0, -0.32, 276.0, 329.0],
  [3981.07, 298.0, -0.25, 276.0, 329.0],
  [5011.87, 298.0, -0.2, 276.0, 329.0],
  [6309.57, 298.0, -0.16, 276.0, 329.0],
  [7943.28, 298.0, -0.13, 276.0, 329.0],
  [10000.0, 298.0, -0.1, 276.0, 329.0],
  [12589.3, 298.0, -0.08, 276.0, 329.0],
  [15848.9, 298.0, -0.06, 276.0, 329.0],
  [19952.6, 298.0, -0.05, 276.0, 329.0],
  [25118.9, 298.0, -0.04, 276.0, 329.0],
  [31622.8, 298.0, -0.03, 276.0, 329.0],
  [39810.7, 298.0, -0.03, 276.0, 329.0],
];

/** The deposit's mean spectrum in the twin's ImpedancePoint shape (ascending frequency). */
export const REFERENCE_SPECTRUM: EisSpectrum = REFERENCE_POINTS.map(([freq, re, im]) => ({
  freq, re, im, mag: Math.hypot(re, im), phase: (Math.atan2(im, re) * 180) / Math.PI,
}));

export const REFERENCE_LABEL = 'Published LIG–nPt aptasensor (Zenodo 10719208)';

export interface ReferenceSource {
  id: string;
  /** short name of the sensor */
  sensor: string;
  /** where the numbers come from */
  citation: string;
  url: string;
  /** measured / reported values, in the source's own terms */
  facts: string[];
  /** how it differs from the simulated graphene-PBASE-aptamer faradaic assay */
  caveat: string;
}

export const REFERENCE_SOURCES: ReferenceSource[] = [
  {
    id: 'zenodo-10719208',
    sensor: 'LIG + nano-Pt aptasensor, E. coli calibration (open spectra)',
    citation: 'McLamore, “Electrochemical Sensor Data (Cavallaro et al)”, Zenodo 2024, CC-BY-4.0',
    url: 'https://doi.org/10.5281/zenodo.10719208',
    facts: [
      'Rs ≈ 297 ± 13 Ω (high-frequency Z′; 3 electrodes × 7 spectra)',
      '|Z| ≈ 0.30 kΩ at 100 Hz, 1.0 kΩ at 1 Hz, 7.9 kΩ at 0.1 Hz',
      'Interfacial capacitance ≈ 150–200 µF (series reading, 0.1–100 Hz)',
      'Scatter of |Z| across electrodes and doses: 1.3 % at 0.1 Hz, 4.6 % at 100 Hz',
      'Dose-to-dose change of |Z|: at most +1.6 % at 0.1 Hz, inside that scatter',
    ],
    caveat: 'Non-faradaic (no redox probe) on a porous, high-area electrode: a capacitive line with no Rct semicircle, and ~100× the twin’s capacitance.',
  },
  {
    id: 'scirep-2026',
    sensor: 'Aptamer / AuNPs / rGO on glassy carbon, E. coli O157:H7',
    citation: 'Sci. Rep. 2026, doi:10.1038/s41598-025-32516-7',
    url: 'https://doi.org/10.1038/s41598-025-32516-7',
    facts: [
      'Rct 117 Ω bare → 102 Ω AuNPs/rGO → 153 Ω with aptamer → 196 Ω after 6×10⁵ CFU/mL (+28 %)',
      '5 mM [Fe(CN)₆]³⁻/⁴⁻ in 0.1 M KCl; 5 mHz–100 kHz, 10 mV',
      '60 min incubation; LOD 3.25 CFU/mL (by DPV)',
    ],
    caveat: 'Faradaic like the twin, but a macro-electrode: Rct is ~10× lower and the binding gain far smaller than the twin’s.',
  },
  {
    id: 'sensors-2020',
    sensor: 'Aptamer on screen-printed electrode in a 3D-printed flow cell, E. coli Crooks',
    citation: 'Sensors 2020, 20, 4421, doi:10.3390/s20164421',
    url: 'https://doi.org/10.3390/s20164421',
    facts: [
      'ΔRct 86 ± 32 at 10⁵ cells/mL rising to 250 ± 40 at 10⁸ cells/mL (as reported)',
      '200 kHz–0.1 Hz, 7 points per decade, 10 mV RMS; modified Randles fit',
      '1 h sample incubation',
    ],
    caveat: 'Same sweep, amplitude and equivalent circuit as the twin; different electrode and linker chemistry.',
  },
  {
    id: 'acsami-2023',
    sensor: 'Laser-reduced graphene oxide + PBASE + capture antibody, E. coli',
    citation: 'ACS Appl. Mater. Interfaces 2023, doi:10.1021/acsami.2c20859',
    url: 'https://doi.org/10.1021/acsami.2c20859',
    facts: [
      'PBASE 2 mg/mL in DMF, 4 h, on graphene — the same linker chemistry as the twin',
      'LOD 283 CFU/mL; range 917 – 2.1×10⁷ CFU/mL',
      '0.5 mM [Fe(CN)₆]³⁻/⁴⁻ in 0.1 M KCl',
    ],
    caveat: 'Read by chronoamperometry with an antibody, not by EIS with an aptamer.',
  },
];
