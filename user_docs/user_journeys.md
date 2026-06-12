# Kognisant 🧠 — User Journey Scenarios

This guide provides simple, step-by-step stories (journeys) to help you understand exactly how to use Kognisant. Whether you are a professional coder or just starting out, these scenarios show you how to talk to your AI assistant and get things done.

---

## Scenario 1: The "New Project" Setup
**Goal**: You have a folder full of code, and you want Kognisant to "learn" it so it can help you.

1.  **Open your Terminal** (Command Prompt, PowerShell, or Terminal).
2.  **Go to your project folder**:
    ```bash
    cd path/to/your/project
    ```
3.  **Wake up Kognisant**:
    Type `kognisant init` and press Enter.
    *   *What happens?* Kognisant creates a hidden "brain" folder called `.kognisant`. This folder tracks what you've built so the AI doesn't forget.
4.  **Start a conversation**:
    Type `kognisant chat`.
5.  **Confirm the files**:
    Type `/files` inside the chat.
    *   *Why do this?* Kognisant will list all the files it can see. This confirms the AI "knows" your project.

---

## Scenario 2: The "Daily Helper" Workflow
**Goal**: You want to know how a specific part of your code works or find a bug.

1.  **Start Chat**: Run `kognisant chat`.
2.  **Feed the AI a file**:
    If you want help with a file named `login.py`, type:
    ```
    /read login.py
    ```
    *   *What happens?* Kognisant "reads" the whole file into its current memory so it can see the actual code.
3.  **Ask your question**:
    "Can you explain how the password validation works in this file?"
4.  **Ask for a fix**:
    "I'm getting an error on line 10. Can you fix it?"
    *   *Visual Tip*: You will see a **PLAN** (what it wants to do), **EXECUTION** (doing the work), and **RESULT** (the final status).

---

## Scenario 3: The "Autonomous Agent" Swarm
**Goal**: You have a big task (like "Write a full test suite for my API") and you want the AI to do the heavy lifting while you grab a coffee.

1.  **Enter the Command**: Inside the chat, use the `/agent` command:
    ```
    /agent Write a complete test file for the calculator module and run it using pytest.
    ```
2.  **Watch the "Swarm" in action**:
    Kognisant will automatically:
    *   **PLAN**: It breaks your big request into smaller pieces (e.g., Read code -> Write test -> Run command).
    *   **EXECUTE**: It creates the files and runs the terminal commands for you in the background.
    *   **REFLECT**: It checks its own work. If the tests fail, it will try to fix the code and run them again automatically.
3.  **Check the Results**:
    Once finished, it will show you exactly what it changed and give you a summary of the success.

---

## Scenario 4: The "Super-Power" Builder (Global Tools)
**Goal**: You wrote a useful script that shrinks image files, and you want Kognisant to be able to use that script in *any* project you work on.

1.  **Identify your script**: Let's say your script is at `scripts/shrink.py`.
2.  **Register it globally**: Inside the chat, use the `/tool` wizard:
    ```
    /tool register shrink scripts/shrink.py
    ```
3.  **The Result**:
    *   Kognisant copies your script to its "Global Core" (`~/.kognisant_core/tools/`).
    *   It creates a "User Manual" for itself so it knows how to run your script.
4.  **Universal Use**:
    Next week, when you are working on a completely different project, you can simply ask:
    "Kognisant, use the shrink tool on the logo.png file."
    **It will work instantly, even though the script isn't in your new project!**

---

## Summary of "Must-Know" Tips

*   **Slash Commands**: These are like "Remote Controls." Use them to peek inside the AI's head (e.g., `/context` shows the AI's current checklist).
*   **Safety First**: Kognisant will never delete your system files. It is "sandboxed" to only touch your project files and its own global tools.
*   **The Global Brain**: Remember that "Skills" and "Tools" are global. If you teach Kognisant a skill (like "How to write CSS for dark mode"), it will remember that skill for every project you ever use it on.

---
*Kognisant is built to be your partner. Don't be afraid to talk to it like a human—it's designed to understand your intent and handle the technical details for you.*
