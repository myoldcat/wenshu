import React from 'react';
import { createRoot } from 'react-dom/client';
import WritingOperations from './WritingOperations.jsx';
import './writing-operations.css';
import './civic-card-selection.css';

createRoot(document.getElementById('root')).render(<React.StrictMode><WritingOperations /></React.StrictMode>);
