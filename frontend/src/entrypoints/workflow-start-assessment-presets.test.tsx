import { act, fireEvent } from '@testing-library/react';
import { assessmentPresetJourneyCases } from '../utils/assessmentPresetJourneyCases';

// Exercise the same Create state/admission boundary locally. Native keyboard,
// focus and navigation behavior is covered separately by the browser entrypoint.
assessmentPresetJourneyCases({
  click: async (element) => { await act(async () => { fireEvent.click(element); }); },
  fill: async (element, value) => { await act(async () => { fireEvent.change(element, { target: { value } }); }); },
  selectOptions: async (element, value) => { await act(async () => { fireEvent.change(element, { target: { value } }); }); },
}, { viewport: async () => {} });
