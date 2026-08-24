
(() => {
  let scanning = false;
  let stopRequested = false;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const normalize = value => (value || "").replace(/\s+/g, " ").trim();

  async function publish(progress) {
    await chrome.storage.local.set({ scanProgress: progress });
  }

  function parseApplicants(text) {
    const value = normalize(text);

    let match = value.match(/\bover\s+(\d{1,3}(?:,\d{3})*)\s+people\s+clicked\s+apply\b/i);
    if (match) {
      return {
        count: Number(match[1].replace(/,/g, "")),
        isOver: true,
        text: match[0]
      };
    }

    match = value.match(/\bover\s+(\d{1,3}(?:,\d{3})*)\s+applicants?\b/i);
    if (match) {
      return {
        count: Number(match[1].replace(/,/g, "")),
        isOver: true,
        text: match[0]
      };
    }

    match = value.match(/\b(\d{1,3}(?:,\d{3})*)\s+people\s+clicked\s+apply\b/i);
    if (match) {
      return {
        count: Number(match[1].replace(/,/g, "")),
        isOver: false,
        text: match[0]
      };
    }

    match = value.match(/\b(\d{1,3}(?:,\d{3})*)\+?\s+applicants?\b/i);
    if (match) {
      return {
        count: Number(match[1].replace(/,/g, "")),
        isOver: false,
        text: match[0]
      };
    }

    return null;
  }

  function currentJobId() {
    try {
      const url = new URL(location.href);
      return (
        url.searchParams.get("currentJobId") ||
        url.searchParams.get("currentJob") ||
        url.pathname.match(/\/jobs\/view\/(\d+)/)?.[1] ||
        null
      );
    } catch {
      return null;
    }
  }

  function cardContainerFromDismissButton(button) {
    let node = button;

    for (let level = 0; level < 10 && node?.parentElement; level += 1) {
      node = node.parentElement;

      const dismissButtons = node.querySelectorAll(
        'button[aria-label^="Dismiss "][aria-label$=" job"]'
      );
      const rect = node.getBoundingClientRect();
      const text = normalize(node.innerText);

      if (
        dismissButtons.length === 1 &&
        rect.width >= 250 &&
        rect.height >= 120 &&
        rect.height <= 520 &&
        text.length >= 20
      ) {
        return node;
      }
    }

    return null;
  }

  function extractCardMetadata(card, title) {
    const rawText = card.innerText || "";
    const lines = rawText
      .split("\n")
      .map(normalize)
      .filter(Boolean)
      .filter(line => line !== title)
      .filter(line => !/^\(Verified job\)$/i.test(line));

    const company = lines.find(line =>
      line.length < 120 &&
      !/\$|benefit|alumni|connection|viewed|posted|ago|remote|hybrid|on-site/i.test(line)
    ) || "";

    const location = lines.find(line =>
      /\b(Remote|Hybrid|On-site)\b/i.test(line) ||
      /\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}\b/.test(line) ||
      line === "United States"
    ) || "";

    const salary = normalize(rawText).match(
      /\$[\d,.]+(?:K)?(?:\/(?:yr|year|hr|hour))?\s*-\s*\$[\d,.]+(?:K)?(?:\/(?:yr|year|hr|hour))?/i
    )?.[0] || "";

    const posted = normalize(rawText).match(
      /\b(?:posted\s+)?(?:\d+\s+(?:minute|hour|day|week|month)s?\s+ago|today|yesterday)\b/i
    )?.[0] || "";

    return { company, location, salary, posted };
  }

  function collectCards(maxJobs) {
    const buttons = [
      ...document.querySelectorAll(
        'button[aria-label^="Dismiss "][aria-label$=" job"]'
      )
    ];

    const cards = [];
    const seenCards = new Set();

    for (const button of buttons) {
      const card = cardContainerFromDismissButton(button);
      if (!card || seenCards.has(card)) continue;

      const rect = card.getBoundingClientRect();
      if (rect.left > window.innerWidth * 0.52) continue;

      const title = (button.getAttribute("aria-label") || "")
        .replace(/^Dismiss\s+/i, "")
        .replace(/\s+job$/i, "")
        .trim();

      if (!title) continue;

      const metadata = extractCardMetadata(card, title);
      seenCards.add(card);

      cards.push({
        card,
        title,
        ...metadata
      });

      if (cards.length >= maxJobs) break;
    }

    return cards;
  }

  function findClickableTitle(card, title) {
    const candidates = [
      ...card.querySelectorAll("span, p, div")
    ].filter(element => {
      const text = normalize(element.innerText);
      const rect = element.getBoundingClientRect();

      return (
        rect.width > 0 &&
        rect.height > 0 &&
        (
          text === title ||
          text === `${title} (Verified job)` ||
          text.startsWith(`${title} `)
        )
      );
    });

    const preferred = candidates.find(element =>
      element.getAttribute("aria-hidden") === "true" &&
      normalize(element.innerText).startsWith(title)
    );

    return preferred?.closest("p") || candidates[0]?.closest("p") || card;
  }

  function rightPaneTitleElement(expectedTitle = null) {
    const elements = [
      ...document.querySelectorAll("h1, h2, p, span")
    ];

    const matching = elements.filter(element => {
      const rect = element.getBoundingClientRect();
      const text = normalize(element.innerText);

      if (
        rect.left < window.innerWidth * 0.36 ||
        rect.width <= 0 ||
        rect.height <= 0 ||
        text.length < 3 ||
        text.length > 240
      ) {
        return false;
      }

      if (expectedTitle) {
        return (
          text === expectedTitle ||
          text === `${expectedTitle} (Verified job)`
        );
      }

      return true;
    });

    return matching[0] || null;
  }

  function selectedJobTitle() {
    const headings = [...document.querySelectorAll("h1")].filter(element => {
      const rect = element.getBoundingClientRect();
      const text = normalize(element.innerText);
      return rect.left >= window.innerWidth * 0.32 && rect.width > 0 && text.length >= 3;
    });
    return normalize(headings[0]?.innerText).replace(/\s+\(Verified job\)$/i, "");
  }

  function rightPaneRoot(expectedTitle = null) {
    const titleElement = rightPaneTitleElement(expectedTitle);

    if (!titleElement) {
      return document.querySelector("main") || document.body;
    }

    let node = titleElement;

    for (let level = 0; level < 10 && node?.parentElement; level += 1) {
      node = node.parentElement;

      const rect = node.getBoundingClientRect();
      const text = normalize(node.innerText);
      const containsApplicantText =
        /people clicked apply/i.test(text) ||
        /\bover\s+\d[\d,]*\s+applicants?\b/i.test(text) ||
        /\b\d[\d,]*\+?\s+applicants?\b/i.test(text);

      if (
        rect.left >= window.innerWidth * 0.32 &&
        rect.width > 300 &&
        text.length > 30 &&
        text.length < 8000 &&
        containsApplicantText
      ) {
        return node;
      }
    }

    return titleElement.parentElement || document.body;
  }

  async function waitUntilSelected(expectedTitle, previousJobId, timeoutMs = 10000) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < timeoutMs) {
      const titleElement = rightPaneTitleElement(expectedTitle);
      const newJobId = currentJobId();
      const jobChanged =
        !!newJobId &&
        newJobId !== previousJobId;

      if (titleElement && (jobChanged || !previousJobId)) {
        await sleep(700);
        return true;
      }

      await sleep(150);
    }

    return false;
  }

  function clickCard(cardItem) {
    const target = findClickableTitle(cardItem.card, cardItem.title);

    target.scrollIntoView({ block: "center", behavior: "instant" });

    target.dispatchEvent(new PointerEvent("pointerdown", {
      bubbles: true,
      cancelable: true,
      pointerType: "mouse"
    }));
    target.dispatchEvent(new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      view: window
    }));
    target.dispatchEvent(new MouseEvent("mouseup", {
      bubbles: true,
      cancelable: true,
      view: window
    }));
    target.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true,
      cancelable: true,
      pointerType: "mouse"
    }));
    target.click();
  }

  function extractCurrentJob(cardItem) {
    const root = rightPaneRoot(cardItem.title);
    const text = normalize(root.innerText);
    const applicantInfo = parseApplicants(text);
    const jobId = currentJobId();

    const titleElement = rightPaneTitleElement(cardItem.title);
    const title = normalize(titleElement?.innerText)
      .replace(/\s+\(Verified job\)$/i, "")
      || cardItem.title;

    let company = "";
    const companyLink = [
      ...root.querySelectorAll('a[href*="/company/"]')
    ].find(link => {
      const value = normalize(link.innerText);
      return value && value.length < 120;
    });

    if (companyLink) {
      company = normalize(companyLink.innerText);
    } else {
      company = cardItem.company;
    }

    const location = text.match(
      /\b(?:United States|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2})(?:\s*\((?:Remote|Hybrid|On-site)\))?/i
    )?.[0] || cardItem.location;

    const salary = text.match(
      /\$[\d,.]+(?:K)?(?:\/(?:yr|year|hr|hour))?\s*-\s*\$[\d,.]+(?:K)?(?:\/(?:yr|year|hr|hour))?/i
    )?.[0] || cardItem.salary;

    const posted = text.match(
      /\b(?:reposted\s+)?(?:\d+\s+(?:minute|hour|day|week|month)s?\s+ago|today|yesterday)\b/i
    )?.[0] || cardItem.posted;

    return {
      linkedin_job_id: jobId || `unknown-${Date.now()}`,
      title,
      company,
      location,
      salary_text: salary,
      applicant_count: applicantInfo?.count ?? null,
      applicant_count_is_over: applicantInfo?.isOver ?? false,
      applicant_text: applicantInfo?.text || "",
      easy_apply: /\bEasy Apply\b/i.test(text),
      promoted: /\bPromoted\b/i.test(text),
      posted_text: posted,
      work_mode:
        /\bRemote\b/i.test(text) ? "Remote" :
        /\bHybrid\b/i.test(text) ? "Hybrid" :
        /\bOn-site\b/i.test(text) ? "On-site" : "",
      description: "",
      url: jobId
        ? `https://www.linkedin.com/jobs/view/${jobId}/`
        : location.href,
      source: "linkedin"
    };
  }

  async function postJob(backendUrl, job) {
    const response = await fetch(`${backendUrl}/jobs/upsert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(job)
    });

    if (!response.ok) {
      throw new Error(`Save failed (${response.status})`);
    }
    return response.json();
  }

  async function recordSelectedApplication(backendUrl) {
    const jobId = currentJobId();
    const title = selectedJobTitle();
    if (!jobId || !title) {
      throw new Error("Select an individual LinkedIn job before recording an application.");
    }
    const job = extractCurrentJob({
      title,
      company: "",
      location: "",
      salary: "",
      posted: ""
    });
    const savedJob = await postJob(backendUrl, job);
    const response = await fetch(`${backendUrl}/jobs/${savedJob.id}/record-application`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applied_at: new Date().toISOString() })
    });
    if (!response.ok) {
      throw new Error(`Application record failed (${response.status})`);
    }
    return response.json();
  }

  async function scan(backendUrl, maxJobs) {
    if (scanning) return;

    scanning = true;
    stopRequested = false;

    try {
      const health = await fetch(`${backendUrl}/health`);
      if (!health.ok) {
        throw new Error(`Backend returned ${health.status}`);
      }
    } catch (error) {
      await publish({
        current: 0,
        total: 0,
        saved: 0,
        priority: 0,
        status: `Backend unreachable: ${error.message}`
      });
      scanning = false;
      return;
    }

    const cards = collectCards(maxJobs);
    let saved = 0;
    let priority = 0;

    await publish({
      current: 0,
      total: cards.length,
      saved,
      priority,
      status: cards.length
        ? `Found ${cards.length} job cards`
        : "No LinkedIn result cards found."
    });

    for (let index = 0; index < cards.length; index += 1) {
      if (stopRequested) break;

      const cardItem = cards[index];
      const previousJobId = currentJobId();

      await publish({
        current: index,
        total: cards.length,
        saved,
        priority,
        status: `Opening ${index + 1}/${cards.length}: ${cardItem.title}`
      });

      clickCard(cardItem);

      const loaded = await waitUntilSelected(
        cardItem.title,
        previousJobId
      );

      if (!loaded) {
        await publish({
          current: index + 1,
          total: cards.length,
          saved,
          priority,
          status: `Could not select ${index + 1}/${cards.length}: ${cardItem.title}`
        });
        continue;
      }

      const job = extractCurrentJob(cardItem);

      try {
        await postJob(backendUrl, job);
        saved += 1;

        if (
          job.applicant_count !== null &&
          !job.applicant_count_is_over &&
          job.applicant_count < 100
        ) {
          priority += 1;
        }

        const applicantLabel =
          job.applicant_count === null
            ? "unknown"
            : job.applicant_count_is_over
              ? `>${job.applicant_count}`
              : String(job.applicant_count);

        await publish({
          current: index + 1,
          total: cards.length,
          saved,
          priority,
          status:
            `Scanned ${index + 1}/${cards.length}: ` +
            `${cardItem.title} — ${applicantLabel}`
        });
      } catch (error) {
        await publish({
          current: index + 1,
          total: cards.length,
          saved,
          priority,
          status: `Backend error: ${error.message}`
        });
        break;
      }

      await sleep(450);
    }

    await publish({
      current: cards.length,
      total: cards.length,
      saved,
      priority,
      status: stopRequested
        ? "Scan stopped"
        : `Done. Saved ${saved} jobs.`
    });

    scanning = false;
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "START_SCAN") {
      scan(message.backendUrl, message.maxJobs);
      sendResponse({ ok: true });
      return true;
    }

    if (message?.type === "STOP_SCAN") {
      stopRequested = true;
      sendResponse({ ok: true });
      return true;
    }

    if (message?.type === "RECORD_SELECTED_APPLICATION") {
      recordSelectedApplication(message.backendUrl)
        .then(result => sendResponse({ ok: true, created: result.created }))
        .catch(error => sendResponse({ ok: false, error: error.message }));
      return true;
    }
  });
})();
