import { page, userEvent } from 'vitest/browser';
import { assessmentPresetJourneyCases } from '../utils/assessmentPresetJourneyCases';
import '../styles/dashboard.css';

assessmentPresetJourneyCases(userEvent, page, () => page.getByRole('link', { name: 'Create', exact: true }).click());
