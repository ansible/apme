---
rule_id: L042
validator: native
description: Play/block has high task count.
---

## Complexity (L042)

Play/block has high task count.

### Example: violation

```yaml
- name: Complex play
  hosts: localhost
  tasks:
    - name: Task 1
      ansible.builtin.debug:
        msg: "1"
    - name: Task 2
      ansible.builtin.debug:
        msg: "2"
    - name: Task 3
      ansible.builtin.debug:
        msg: "3"
    - name: Task 4
      ansible.builtin.debug:
        msg: "4"
    - name: Task 5
      ansible.builtin.debug:
        msg: "5"
    - name: Task 6
      ansible.builtin.debug:
        msg: "6"
    - name: Task 7
      ansible.builtin.debug:
        msg: "7"
    - name: Task 8
      ansible.builtin.debug:
        msg: "8"
    - name: Task 9
      ansible.builtin.debug:
        msg: "9"
    - name: Task 10
      ansible.builtin.debug:
        msg: "10"
    - name: Task 11
      ansible.builtin.debug:
        msg: "11"
    - name: Task 12
      ansible.builtin.debug:
        msg: "12"
    - name: Task 13
      ansible.builtin.debug:
        msg: "13"
    - name: Task 14
      ansible.builtin.debug:
        msg: "14"
    - name: Task 15
      ansible.builtin.debug:
        msg: "15"
    - name: Task 16
      ansible.builtin.debug:
        msg: "16"
    - name: Task 17
      ansible.builtin.debug:
        msg: "17"
    - name: Task 18
      ansible.builtin.debug:
        msg: "18"
    - name: Task 19
      ansible.builtin.debug:
        msg: "19"
    - name: Task 20
      ansible.builtin.debug:
        msg: "20"
    - name: Task 21
      ansible.builtin.debug:
        msg: "21"
```

### Example: pass

```yaml
- name: Simple play
  hosts: localhost
  tasks:
    - name: Task 1
      ansible.builtin.debug:
        msg: "1"
```
