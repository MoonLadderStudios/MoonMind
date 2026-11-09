import { userEvent } from 'vitest/browser';
import { assessmentAuthorizationNativeCases } from '../utils/assessmentAuthorizationNativeCases';
import '../styles/dashboard.css';

assessmentAuthorizationNativeCases(userEvent);
