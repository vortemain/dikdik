const KEY = "todos";
const input = document.getElementById("new-todo");
const list = document.getElementById("todo-list");
const count = document.getElementById("count");

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; }
}

let todos = load();

function save() {
  localStorage.setItem(KEY, JSON.stringify(todos));
}

function render() {
  list.replaceChildren(...todos.map((todo, index) => {
    const item = document.createElement("li");
    item.className = todo.done ? "done" : "";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = todo.done;
    box.setAttribute("aria-label", "Done: " + todo.text);
    box.addEventListener("change", () => { todo.done = box.checked; save(); render(); });
    const text = document.createElement("span");
    text.textContent = todo.text;
    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.addEventListener("click", () => { todos.splice(index, 1); save(); render(); });
    item.append(box, text, remove);
    return item;
  }));
  const left = todos.filter(todo => !todo.done).length;
  count.textContent = `${left} ${left === 1 ? "item" : "items"} left`;
}

input.addEventListener("keydown", event => {
  if (event.key !== "Enter") return;
  const text = input.value.trim();
  if (text) {
    todos.push({ text, done: false });
    save();
    render();
  }
  input.value = "";
});

render();
