// Implementation liberally borrowed from a generative model with updates
// using examples from https://github.com/proxy-wasm/proxy-wasm-rust-sdk/tree/main/examples

use proxy_wasm::traits::*;
use proxy_wasm::types::*;
use regex::{Regex, Captures};
use once_cell::sync::Lazy;
use std::borrow::Cow;

static EMAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}").unwrap()
});

static SSN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b").unwrap()
});

fn redact_text(input: &str) -> Cow<'_, str> {
    // If no matches, return input directly
    if !EMAIL_RE.is_match(input) && !SSN_RE.is_match(input) {
        return Cow::Borrowed(input);
    }

    // // This leads to variable length string considerations
    // Allocate a single String once
    // let mut s = input.to_owned();
    // // Apply replacements in-place
    // s = EMAIL_RE.replace_all(&s, "[EMAIL_REDACTED]").into_owned();
    // s = SSN_RE.replace_all(&s, "[SSN_REDACTED]").into_owned();

    let replaced_emails: String = EMAIL_RE.replace_all(input, |caps: &Captures| {
        "*".repeat(caps[0].len())
    }).into_owned();

    // Replace SSNs on the result
    let replaced_ssns: String = SSN_RE.replace_all(&replaced_emails, |caps: &Captures| {
        "*".repeat(caps[0].len())
    }).into_owned();

    Cow::Owned(replaced_ssns)
}

/// HTTP context for request/response handling.
struct PiiFilter {
    id: u32,
}

impl PiiFilter {
    fn new(id: u32) -> Self {
        PiiFilter { id }
    }

    fn redact_and_replace_body(&mut self, body: Vec<u8>, is_request: bool, ) {
        if body.is_empty() {
            return;
        }
        // Try decode as utf-8; if not text, skip
        let s = match std::str::from_utf8(&body) {
            Ok(s) => s,
            Err(_) => {
                log::debug!("Non-UTF8 body; skipping redaction");
                return;
            }
        };

        let redacted = redact_text(s);
            if redacted != s {
                let new_body = redacted.as_bytes();
                log::info!(
                    "Replacing {} body ({} bytes)",
                    if is_request { "request" } else { "response" },
                    new_body.len()
                );
                if is_request {
                    self.set_http_request_body(0, new_body.len(), new_body);
                } else {
                    self.set_http_response_body(0, new_body.len(), new_body);
                }
            }

        // let redacted: &str = &redact_text(s); // convert Cow to &str

        // if redacted != s {
        //     // Pad or truncate redacted string to match original length
        //     // Hacky hacks!
        //     let new_body = if redacted.len() < s.len() {
        //         let mut padded = redacted.to_string();
        //         padded.push_str(&" ".repeat(s.len() - redacted.len()));
        //         padded.into_bytes()
        //     } else if redacted.len() > s.len() {
        //         redacted[..s.len()].as_bytes().to_vec()
        //     } else {
        //         redacted.to_string().into_bytes()
        //     };
        //     log::info!(
        //         "Replacing {} body ({} bytes): {}",
        //         if is_request { "request" } else { "response" },
        //         new_body.len(),
        //         String::from_utf8_lossy(&new_body)
        //     );
        //     if is_request {
        //         // replace whole request body starting at 0
        //         self.set_http_request_body(0, new_body.len(), &new_body);
        //     } else {
        //         self.set_http_response_body(0, new_body.len(), &new_body);
        //     }
        // }
    }
}

impl Context for PiiFilter {}

impl HttpContext for PiiFilter {
    // Called when a chunk of request body is received. We'll wait until end_of_stream == true.
    fn on_http_request_body(&mut self, body_size: usize, end_of_stream: bool) -> Action {
        if end_of_stream {
            if let Some(body) = self.get_http_request_body(0, body_size) {
                self.redact_and_replace_body(body, true);
            }
        }
        Action::Continue
    }

    // Called when a chunk of response body is received.
    fn on_http_response_body(&mut self, body_size: usize, end_of_stream: bool) -> Action {
        if end_of_stream {
            if let Some(body) = self.get_http_response_body(0, body_size) {
                self.redact_and_replace_body(body, false);
            }
        }
        Action::Continue
    }
}

/// Root context factory & registration
struct Root;

impl Context for Root {}

impl RootContext for Root {
    fn on_configure(&mut self, _configuration_size: usize) -> bool {
        // Called once when the VM starts. Can read config via get_property if provided.
        true
    }

    fn create_http_context(&self, context_id: u32) -> Option<Box<dyn HttpContext>> {
        let id = context_id;
        Some(Box::new(PiiFilter::new(id)))
    }

    fn get_type(&self) -> Option<ContextType> {
        Some(ContextType::HttpContext)
    }
}

proxy_wasm::main! {{
    proxy_wasm::set_log_level(LogLevel::Trace);
    proxy_wasm::set_root_context(|_| -> Box<dyn RootContext> { Box::new(Root) });
}}
