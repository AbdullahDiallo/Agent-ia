# 🔍 Audit des boutons "Add" non fonctionnels

**Date :** 7 janvier 2026  
**Problème identifié :** Plusieurs pages ont des boutons "Ajouter" qui ne déclenchent aucune action

---

## 📋 Pages concernées

### ✅ Pages avec bouton Add identifié

| Page | Bouton | Ligne | Action actuelle | Action requise |
|------|--------|-------|-----------------|----------------|
| **FilièresPage.tsx** | "Nouveau bien" | 196-199 | ❌ Aucune | Modal création |
| **PersonnesPage.tsx** | "Nouveau client" | 193-196 | ❌ Aucune | Modal création |
| **CalendarPage.tsx** | À vérifier | - | ❌ Aucune | Modal création RDV |
| **ConversationsPage.tsx** | À vérifier | - | ❌ Aucune | Modal création |
| **EmailsPage.tsx** | À vérifier | - | ❌ Aucune | Modal composer |
| **SmsPage.tsx** | À vérifier | - | ❌ Aucune | Modal envoyer |

---

## 🎯 Plan de correction

### Phase 1 : Filières (Jour 1-2)

#### Backend
1. ✅ Endpoint POST `/kb/filières` existe déjà
2. ❌ Ajouter PUT `/kb/filières/{id}`
3. ❌ Ajouter DELETE `/kb/filières/{id}`

#### Frontend
1. ❌ Créer composant `BienModal.tsx` (création + édition)
2. ❌ Connecter bouton "Nouveau bien" → ouvrir modal
3. ❌ Ajouter actions Edit/Delete dans le tableau
4. ❌ Gérer soumission formulaire → appel API
5. ❌ Toast de succès/erreur

---

### Phase 2 : Personnes (Jour 3-4)

#### Backend
1. ✅ Endpoint POST `/kb/personnes` existe déjà
2. ❌ Ajouter PUT `/kb/personnes/{id}`
3. ❌ Ajouter DELETE `/kb/personnes/{id}`

#### Frontend
1. ❌ Créer composant `ClientModal.tsx` (création + édition)
2. ❌ Connecter bouton "Nouveau client" → ouvrir modal
3. ❌ Ajouter actions Edit/Delete dans le tableau
4. ❌ Gérer soumission formulaire → appel API
5. ❌ Toast de succès/erreur

---

### Phase 3 : Rendez-vous (Jour 5-6)

#### Backend
1. ✅ Endpoint POST `/kb/rendezvous` existe déjà
2. ✅ Endpoint PUT `/kb/rendezvous/{id}` existe déjà
3. ✅ Endpoint DELETE `/kb/rendezvous/{id}` existe déjà
4. ❌ Ajouter GET `/kb/rendezvous` (liste avec filtres)

#### Frontend
1. ❌ Créer composant `RendezVousModal.tsx` (création + édition)
2. ❌ Ajouter bouton "Nouveau rendez-vous"
3. ❌ Implémenter toggle Vue Liste / Vue Calendrier
4. ❌ Intégrer react-big-calendar pour vue calendrier
5. ❌ Ajouter actions Edit/Delete
6. ❌ Filtres par agent, statut, date

---

### Phase 4 : Conversations (Jour 7-8)

#### Backend
1. ✅ Endpoint POST `/kb/conversations` existe déjà
2. ❌ Ajouter PUT `/kb/conversations/{id}`
3. ❌ Ajouter DELETE `/kb/conversations/{id}`
4. ❌ Ajouter PUT `/kb/conversations/{id}/assign` (réassignation)

#### Frontend
1. ❌ Créer composant `ConversationModal.tsx` (création)
2. ❌ Ajouter bouton "Nouvelle conversation"
3. ❌ Ajouter actions Edit/Delete/Assign
4. ❌ Modal de réassignation à un agent

---

## 🛠️ Composants réutilisables à créer

### 1. Modal générique
```typescript
// components/ui/Modal.tsx
interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
}
```

### 2. ConfirmDialog
```typescript
// components/ui/ConfirmDialog.tsx
interface ConfirmDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'warning' | 'info';
}
```

### 3. FormField
```typescript
// components/ui/FormField.tsx
interface FormFieldProps {
  label: string;
  name: string;
  type?: 'text' | 'email' | 'number' | 'textarea' | 'select';
  value: any;
  onChange: (value: any) => void;
  error?: string;
  required?: boolean;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
}
```

---

## 📝 Checklist de validation

### Pour chaque page
- [ ] Bouton "Add" ouvre une modal
- [ ] Modal contient un formulaire validé
- [ ] Soumission appelle l'API backend
- [ ] Toast de succès s'affiche
- [ ] Liste se rafraîchit automatiquement
- [ ] Gestion des erreurs avec messages clairs
- [ ] Actions Edit/Delete fonctionnelles
- [ ] Confirmation avant suppression
- [ ] Permissions vérifiées (RoleGuard)

---

**Prochaine étape :** Commencer par FilièresPage (backend PUT/DELETE + frontend modal)
